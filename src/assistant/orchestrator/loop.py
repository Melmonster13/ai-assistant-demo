"""The hand-controlled tool-calling loop — the single enforcement point.

Every tool call, whether triggered by the user's request or by the model reacting
to tool output, passes through the same gate: high-tier (destructive) calls need
per-call confirmation and a single-use argument-bound token; low-tier (read-only)
calls ride a longer-lived tool-scoped token, so every boundary still verifies a
signed permission slip. Tool output feeds back to the model as data only. Tool
definitions are fingerprint-checked at discovery (TOFU + drift); a tool whose
definition changed this session is confirmation-forced regardless of tier.

Memory is a direct integration, not an MCP tool: persona/profile is injected into
the system prompt, relevant facts are auto-recalled per turn, and remember_fact is
handled inline (internal write, low-risk, no wrapper/token) but still audited. A
destructive action suggested by a recalled fact still hits the confirmation gate,
so memory can't become a prompt-injection bypass.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from assistant.audit import log as audit
from assistant.config import ToolServer
from assistant.guardrails import broker, confirmation, registry
from assistant.memory.facts import FactStore
from assistant.model.base import ModelAdapter, ToolCall

REMEMBER_TOOL = {
    "name": "remember_fact",
    "description": (
        "Save a durable fact about the user or their world for future recall. "
        "Use when the user shares a lasting preference, detail, or something "
        "they'd expect you to remember later."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact to remember, as a self-contained sentence.",
            }
        },
        "required": ["content"],
    },
}


@dataclass
class _Route:
    server: ToolServer
    force_confirm: bool


class Orchestrator:
    def __init__(
        self,
        model: ModelAdapter,
        *,
        conn: Any,
        tool_servers: tuple[ToolServer, ...],
        private_key: str,
        ttl_seconds: int,
        low_ttl_seconds: int,
        user_id: str,
        system_prompt: str | None = None,
        fact_store: FactStore | None = None,
        recall_k: int = 5,
        recall_min_similarity: float = 0.25,
        confirm: Callable[[str, dict[str, Any]], bool] = confirmation.confirm,
        approve: Callable[[str], bool] = confirmation.approve,
    ) -> None:
        self._model = model
        self._conn = conn
        self._tool_servers = tool_servers
        self._private_key = private_key
        self._ttl_seconds = ttl_seconds
        self._low_ttl_seconds = low_ttl_seconds
        self._user_id = user_id
        self._system_prompt = system_prompt
        self._fact_store = fact_store
        self._recall_k = recall_k
        self._recall_min_similarity = recall_min_similarity
        self._confirm = confirm
        self._approve = approve
        self._messages: list[Any] = []
        self._routes: dict[str, _Route] = {}
        self._tools: list[dict[str, Any]] = []
        self._low_tokens: dict[str, tuple[str, float]] = {}  # tool -> (token, expiry)
        # internal (direct-integration) tools: handled inline, never routed to a wrapper
        self._internal: dict[str, Callable[[dict[str, Any]], str]] = {}
        if fact_store is not None:
            self._internal["remember_fact"] = self._remember_fact
            self._tools.append(REMEMBER_TOOL)

    @property
    def tool_names(self) -> list[str]:
        return [t["name"] for t in self._tools]

    def startup(self) -> list[str]:
        """Discover tools from each wrapper and run them through the fingerprint
        registry (TOFU + drift). Only cleared tools are offered to the model.
        Unreachable servers are skipped gracefully. Returns discovery notes."""
        notes = []
        for server in self._tool_servers:
            try:
                response = httpx.get(f"{server.url}/tools", timeout=10)
                definitions = response.json()["tools"]
            except (httpx.HTTPError, KeyError, ValueError):
                notes.append(f"[{server.server_id}] unavailable — its tools are offline this session")
                continue
            cleared = registry.reconcile(
                self._conn, server.server_id, definitions, approve=self._approve, user_id=self._user_id
            )
            for tool in cleared:
                name = tool.definition["name"]
                if name in self._routes or name in self._internal:
                    raise RuntimeError(f"tool name collision: {name}")
                self._routes[name] = _Route(server, tool.force_confirm)
                self._tools.append(
                    {
                        "name": name,
                        "description": tool.definition["description"],
                        "input_schema": tool.definition["input_schema"],
                    }
                )
            notes.append(f"[{server.server_id}] {len(cleared)} tool(s) cleared (tier={server.tier})")
        return notes

    def run_turn(self, user_input: str) -> str:
        # auto-recall: relevant facts for this turn are assembled fresh into the
        # system prompt (system is passed per-call, so it can vary each turn)
        system = self._system_prompt or ""
        recalled = self._recall(user_input)
        if recalled:
            system = f"{system}\n\n{recalled}".strip()

        self._messages.append(self._model.user_message(user_input))
        triggered_by = "user_request"
        while True:
            response = self._model.complete(self._messages, self._tools, system=system or None)
            self._messages.append(response.raw_message)
            if not response.tool_calls:
                return response.text or ""
            for call in response.tool_calls:
                result = self._handle_tool_call(call, triggered_by)
                self._messages.append(self._model.tool_result_message(call.id, result))
            # anything the model asks for after this round is a reaction to tool output
            triggered_by = "tool_output"

    def _recall(self, query: str) -> str:
        if self._fact_store is None:
            return ""
        facts = self._fact_store.recall(
            self._user_id, query, k=self._recall_k, min_similarity=self._recall_min_similarity
        )
        if not facts:
            return ""
        audit.record(
            self._conn,
            user_id=self._user_id,
            tool_name="memory",
            event="memory_recalled",
            triggered_by="user_request",
            detail=f"{len(facts)} fact(s)",
        )
        lines = "\n".join(f"- {f.content}" for f in facts)
        return f"Relevant things you remember about the user:\n{lines}"

    def _remember_fact(self, arguments: dict[str, Any]) -> str:
        content = (arguments.get("content") or "").strip()
        if not content:
            return "Nothing to remember (empty content)."
        self._fact_store.remember(self._user_id, content)
        return f"Saved to memory: {content}"

    def _handle_tool_call(self, call: ToolCall, triggered_by: str) -> str:
        def record(event: str, detail: str | None = None, jti: str | None = None) -> None:
            audit.record(
                self._conn,
                user_id=self._user_id,
                tool_name=call.name,
                event=event,
                triggered_by=triggered_by,
                detail=detail,
                jti=jti,
            )

        if call.name in self._internal:
            # direct integration: internal, low-risk, no wrapper/token — but audited
            record("requested", detail=json.dumps(call.arguments))
            result = self._internal[call.name](call.arguments)
            record("executed", detail=result)
            return result

        route = self._routes.get(call.name)
        if route is None:
            record("requested", detail="unknown tool — not cleared by the registry")
            return f"Tool '{call.name}' is not available."

        record("requested", detail=json.dumps(call.arguments))
        tier = route.server.tier

        if tier == "high" or route.force_confirm:
            if not self._confirm(call.name, call.arguments):
                record("denied")
                return "The user denied this tool call."
            record("confirmed")

        if tier == "high":
            minted = broker.mint_token(
                call.name,
                call.arguments,
                self._user_id,
                private_key=self._private_key,
                ttl_seconds=self._ttl_seconds,
                conn=self._conn,
                tier="high",
            )
            record("token_minted", jti=minted.jti)
            token, jti = minted.token, minted.jti
        else:
            token, jti = self._low_tier_token(call.name, record)

        try:
            response = httpx.post(
                f"{route.server.url}/execute",
                json={"tool_name": call.name, "arguments": call.arguments, "token": token},
                timeout=30,
            )
            payload = response.json()
        except httpx.HTTPError as exc:
            record("rejected_by_wrapper", detail=f"wrapper unreachable: {exc}", jti=jti)
            return "This tool is currently unavailable."

        if response.status_code == 200 and payload.get("status") == "executed":
            record("executed", detail=payload.get("result"), jti=jti)
            return payload.get("result", "")

        reason = payload.get("reason", "unknown")
        record("rejected_by_wrapper", detail=reason, jti=jti)
        return f"Tool call rejected at the tool boundary: {reason}"

    def _low_tier_token(self, tool_name: str, record: Callable[..., None]) -> tuple[str, str]:
        cached = self._low_tokens.get(tool_name)
        if cached is not None and time.time() < cached[1] - 5:
            return cached[0], ""
        minted = broker.mint_token(
            tool_name,
            {},
            self._user_id,
            private_key=self._private_key,
            ttl_seconds=self._low_ttl_seconds,
            conn=self._conn,
            tier="low",
        )
        record("token_minted", jti=minted.jti)
        self._low_tokens[tool_name] = (minted.token, time.time() + self._low_ttl_seconds)
        return minted.token, minted.jti

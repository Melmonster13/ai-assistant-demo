"""The hand-controlled tool-calling loop — the single enforcement point.

Every tool call, whether triggered by the user's request or by the model reacting
to tool output, passes through the same gate: high-tier (destructive) calls need
per-call confirmation and a single-use argument-bound token; low-tier (read-only)
calls ride a longer-lived tool-scoped token, so every boundary still verifies a
signed permission slip. Tool output feeds back to the model as data only. Tool
definitions are fingerprint-checked at discovery (TOFU + drift); a tool whose
definition changed this session is confirmation-forced regardless of tier.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from assistant.audit import log as audit
from assistant.config import ToolServer
from assistant.guardrails import broker, confirmation, registry
from assistant.model.base import ModelAdapter, ToolCall


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
        self._confirm = confirm
        self._approve = approve
        self._messages: list[Any] = []
        self._routes: dict[str, _Route] = {}
        self._tools: list[dict[str, Any]] = []
        self._low_tokens: dict[str, tuple[str, float]] = {}  # tool -> (token, expiry)

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
                if name in self._routes:
                    raise RuntimeError(f"tool name collision across servers: {name}")
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
        self._messages.append(self._model.user_message(user_input))
        triggered_by = "user_request"
        while True:
            response = self._model.complete(self._messages, self._tools, system=self._system_prompt)
            self._messages.append(response.raw_message)
            if not response.tool_calls:
                return response.text or ""
            for call in response.tool_calls:
                result = self._handle_tool_call(call, triggered_by)
                self._messages.append(self._model.tool_result_message(call.id, result))
            # anything the model asks for after this round is a reaction to tool output
            triggered_by = "tool_output"

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

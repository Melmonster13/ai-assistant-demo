"""The hand-controlled tool-calling loop — the single enforcement point.

Every tool call, whether triggered by the user's request or by the model reacting
to tool output, passes through the same confirm → mint → wrapper-verify path.
Tool output is fed back to the model as data only; it cannot reach the wrapper
without a fresh confirmation and token. The wrapper, not this loop, decides
whether a token is valid.
"""

import json
from typing import Any

import httpx

from assistant.audit import log as audit
from assistant.guardrails import broker, confirmation
from assistant.model.base import ModelAdapter, ToolCall


class Orchestrator:
    def __init__(
        self,
        model: ModelAdapter,
        *,
        conn: Any,
        tools: list[dict[str, Any]],
        wrapper_url: str,
        private_key: str,
        ttl_seconds: int,
        user_id: str,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model
        self._conn = conn
        self._tools = tools
        self._wrapper_url = wrapper_url
        self._private_key = private_key
        self._ttl_seconds = ttl_seconds
        self._user_id = user_id
        self._system_prompt = system_prompt
        self._messages: list[Any] = []

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
        arguments_json = json.dumps(call.arguments)

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

        record("requested", detail=arguments_json)

        if not confirmation.confirm(call.name, call.arguments):
            record("denied")
            return "The user denied this tool call."
        record("confirmed")

        minted = broker.mint_token(
            call.name,
            call.arguments,
            self._user_id,
            private_key=self._private_key,
            ttl_seconds=self._ttl_seconds,
            conn=self._conn,
        )
        record("token_minted", jti=minted.jti)

        try:
            response = httpx.post(
                f"{self._wrapper_url}/execute",
                json={"tool_name": call.name, "arguments": call.arguments, "token": minted.token},
                timeout=10,
            )
            payload = response.json()
        except httpx.HTTPError as exc:
            record("rejected_by_wrapper", detail=f"wrapper unreachable: {exc}", jti=minted.jti)
            return "This tool is currently unavailable."

        if response.status_code == 200 and payload.get("status") == "executed":
            record("executed", detail=payload.get("result"), jti=minted.jti)
            return payload.get("result", "")

        reason = payload.get("reason", "unknown")
        record("rejected_by_wrapper", detail=reason, jti=minted.jti)
        return f"Tool call rejected at the tool boundary: {reason}"

"""Claude implementation of the model adapter."""

from typing import Any

import anthropic

from assistant.model.base import ModelResponse, ToolCall


class ClaudeAdapter:
    def __init__(self, api_key: str, model: str = "claude-sonnet-5") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]],
        system: str | None = None,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 1024,
            "messages": messages,
            "tools": tools,
        }
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        content: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})

        return ModelResponse(
            text="\n".join(text_parts) or None,
            tool_calls=tool_calls,
            raw_message={"role": "assistant", "content": content},
        )

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_result_message(self, tool_call_id: str, content: str) -> Any:
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call_id, "content": content}],
        }

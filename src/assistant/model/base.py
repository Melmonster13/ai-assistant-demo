"""Model adapter interface. The orchestrator talks to this, never to a vendor SDK —
provider choice is config, not architecture. Message objects are opaque to the
orchestrator: it appends what the adapter hands back (raw_message, user_message,
tool_result_message) without inspecting provider format."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: Any = None


class ModelAdapter(Protocol):
    def complete(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]],
        system: str | None = None,
    ) -> ModelResponse: ...

    def user_message(self, text: str) -> Any: ...

    def tool_result_message(self, tool_call_id: str, content: str) -> Any: ...

"""Decision queue bridging the orchestrator's blocking confirm/approve callables
to an asynchronous UI. The tool-call worker thread blocks in ask(); the browser
polls pending() and answers via resolve(). Timeout or shutdown resolves to deny —
the gate fails closed."""

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Pending:
    id: str
    kind: str  # "confirm" (per-call) | "approve" (TOFU / drift)
    payload: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    decision: bool | None = None


class DecisionQueue:
    def __init__(self, timeout_seconds: float = 300.0) -> None:
        self._timeout = timeout_seconds
        self._items: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def ask(self, kind: str, payload: dict[str, Any]) -> bool:
        item = _Pending(id=uuid.uuid4().hex, kind=kind, payload=payload)
        with self._lock:
            self._items[item.id] = item
        item.event.wait(self._timeout)
        with self._lock:
            self._items.pop(item.id, None)
        return item.decision is True  # None (timeout) -> deny

    def confirm(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return self.ask("confirm", {"tool_name": tool_name, "arguments": arguments})

    def approve(self, prompt: str) -> bool:
        return self.ask("approve", {"prompt": prompt})

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"id": item.id, "kind": item.kind, **item.payload}
                for item in self._items.values()
                if item.decision is None
            ]

    def resolve(self, item_id: str, allow: bool) -> bool:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.decision is not None:
                return False
            item.decision = allow
        item.event.set()
        return True

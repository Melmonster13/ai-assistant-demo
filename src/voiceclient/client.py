"""HTTP client for the assistant-web API — the same endpoints the browser uses.
While a chat turn is in flight, pending confirmations/approvals are polled and
answered through the caller's decision callback (spoken, for the voice loop).
The web UI polls the same queue, so whichever surface answers first wins."""

import threading
import time
from typing import Any, Callable

import httpx


class AssistantClient:
    def __init__(self, base_url: str, *, poll_interval: float = 0.5, chat_timeout: float = 600) -> None:
        self._base = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._chat_timeout = chat_timeout

    def chat(self, message: str, on_decision: Callable[[dict[str, Any]], bool]) -> str:
        result: dict[str, Any] = {}

        def post_chat() -> None:
            try:
                result["response"] = httpx.post(
                    f"{self._base}/api/chat", json={"message": message}, timeout=self._chat_timeout
                )
            except httpx.HTTPError as exc:
                result["error"] = exc

        worker = threading.Thread(target=post_chat, daemon=True)
        worker.start()

        answered: set[str] = set()
        while worker.is_alive():
            worker.join(self._poll_interval)
            if not worker.is_alive():
                break
            for item in self._pending():
                if item["id"] in answered:
                    continue
                allow = on_decision(item)
                try:
                    httpx.post(
                        f"{self._base}/api/decision",
                        json={"id": item["id"], "allow": allow},
                        timeout=5,
                    )
                except httpx.HTTPError:
                    pass  # timed out server-side -> deny; nothing to do
                answered.add(item["id"])

        if "error" in result:
            return "I couldn't reach the assistant."
        response = result["response"]
        if response.status_code == 409:
            return "I'm still working on the previous request."
        if response.status_code != 200:
            return "Something went wrong handling that."
        return response.json().get("reply", "")

    def _pending(self) -> list[dict[str, Any]]:
        try:
            return httpx.get(f"{self._base}/api/pending", timeout=5).json()["items"]
        except (httpx.HTTPError, KeyError, ValueError):
            return []

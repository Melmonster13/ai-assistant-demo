"""Bridge between the sync HTTP wrapper and a stdio MCP server it owns.

This settles Phase 2's one real design unknown: the verifying wrapper spawns the
MCP server as its child process and holds the only client session to it, so the
token check is structurally in front — there is no path to the tool that skips
verification. The MCP session runs on a background event loop thread; the HTTP
handler calls in synchronously.
"""

import asyncio
import threading
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


class McpBridge:
    def __init__(self, command: str, args: list[str], env: dict[str, str]) -> None:
        # explicit env only — the MCP server must not inherit the wrapper's
        # full environment (least privilege per tool)
        self._params = StdioServerParameters(
            command=command,
            args=args,
            env={**get_default_environment(), **env},
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._ready = threading.Event()
        self._session: ClientSession | None = None
        self._stop_event: asyncio.Event | None = None
        self._future = None

    async def _run(self) -> None:
        self._stop_event = asyncio.Event()
        try:
            async with stdio_client(self._params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._stop_event.wait()
        finally:
            self._session = None
            self._ready.set()  # unblock start() if startup failed

    def start(self, timeout: float = 30) -> None:
        self._thread.start()
        self._future = asyncio.run_coroutine_threadsafe(self._run(), self._loop)
        self._ready.wait(timeout)
        if self._session is None:
            error = self._future.exception() if self._future.done() else "timed out"
            raise RuntimeError(f"MCP server failed to start: {error}")

    def _call(self, coro: Any, timeout: float = 30) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._call(self._session.list_tools())
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
            for t in result.tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Returns (ok, text). ok=False is a tool-level error, not a security event."""
        result = self._call(self._session.call_tool(name, arguments))
        text = "\n".join(b.text for b in result.content if getattr(b, "type", None) == "text")
        return (not result.isError, text)

    def stop(self) -> None:
        if self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._future is not None:
            try:
                self._future.result(timeout=10)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

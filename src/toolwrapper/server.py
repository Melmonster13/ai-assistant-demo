"""Verifying wrapper: an HTTP proxy in front of one stdio MCP server it spawns
and owns. A separate process from the orchestrator, holding only the public key,
so the gate is a real boundary, not a function call the orchestrator could skip.

GET  /tools    -> tool definitions from the live MCP session (for discovery and
                  fingerprinting on the orchestrator side)
POST /execute  -> JSON {tool_name, arguments, token}: verify per this wrapper's
                  tier, then forward to the MCP server; 403 with nothing
                  executed on any verification failure.

Run: `tool-wrapper <server_id>` with server_id from servers.toml.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import psycopg

from toolwrapper.bridge import McpBridge
from toolwrapper.config import load_config
from toolwrapper.verify import TokenRejected, verify_token

# Content-Length is client-controlled; cap it so a declared-huge body can't force
# the wrapper to allocate/read arbitrary memory. 1 MB is generous for the
# {tool_name, arguments, token} payload shape.
MAX_BODY_BYTES = 1024 * 1024


class WrapperServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, public_key: str, conn: Any, *, tier: str, bridge: McpBridge) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.public_key = public_key
        self.conn = conn
        self.tier = tier
        self.bridge = bridge
        self.executions: list[dict[str, Any]] = []
        # Serialize the jti-consuming UPDATE across request threads. Postgres
        # row-locking already makes single-use atomic, but this makes the
        # guarantee explicit and independent of psycopg's shared-connection
        # threading semantics. Held only around the DB op, never the tool call.
        self.db_lock = threading.Lock()

    @property
    def port(self) -> int:
        return self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server: WrapperServer

    def do_GET(self) -> None:
        if self.path != "/tools":
            self._respond(404, {"status": "error", "reason": "not found"})
            return
        self._respond(200, {"tier": self.server.tier, "tools": self.server.bridge.list_tools()})

    def do_POST(self) -> None:
        if self.path != "/execute":
            self._respond(404, {"status": "error", "reason": "not found"})
            return

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._respond(400, {"status": "error", "reason": "invalid Content-Length"})
            return
        if length < 0:
            self._respond(400, {"status": "error", "reason": "invalid Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            # reject before reading — never allocate for a client-declared size
            self._respond(413, {"status": "error", "reason": "request body too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"status": "error", "reason": "invalid JSON body"})
            return

        tool_name = body.get("tool_name", "")
        arguments = body.get("arguments") or {}
        token = body.get("token")

        try:
            if not token:
                raise TokenRejected("missing token")
            verify_token(
                token,
                tool_name,
                arguments,
                public_key=self.server.public_key,
                conn=self.server.conn,
                tier=self.server.tier,
                db_lock=self.server.db_lock,
            )
        except TokenRejected as exc:
            self._respond(403, {"status": "rejected", "reason": exc.reason})
            return

        self.server.executions.append({"tool_name": tool_name, "arguments": arguments})
        try:
            ok, text = self.server.bridge.call_tool(tool_name, arguments)
        except Exception as exc:
            self._respond(502, {"status": "error", "reason": f"MCP server error: {exc}"})
            return
        print(f"EXECUTED [{self.server.tier}]: {tool_name} {json.dumps(arguments)}", flush=True)
        self._respond(200, {"status": "executed", "result": text, "tool_error": not ok})

    def _respond(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # own prints above; suppress per-request access lines


def main() -> None:
    import os

    if len(sys.argv) != 2:
        raise SystemExit("usage: tool-wrapper <server_id>")
    cfg = load_config(sys.argv[1])
    spec = cfg.server
    bridge = McpBridge(
        sys.executable,
        ["-m", spec.module],
        {key: os.environ[key] for key in spec.env_keys if key in os.environ},
    )
    bridge.start()
    conn = psycopg.connect(cfg.database_url, autocommit=True)
    server = WrapperServer(spec.port, cfg.jwt_public_key_path.read_text(), conn, tier=spec.tier, bridge=bridge)
    print(f"[{spec.server_id}] wrapper on 127.0.0.1:{server.port} (tier={spec.tier}, mcp={spec.module})", flush=True)
    try:
        server.serve_forever()
    finally:
        bridge.stop()

"""Fake destructive tool behind the verifying wrapper — a separate process, so the
gate is a real boundary, not a function call the orchestrator could skip.

POST /execute with JSON {tool_name, arguments, token}. Verification first; only
on success does the fake tool "execute" (it just reports what it would have
done). Phase 2 replaces the fake tool with a proxy to a real MCP server; the
verify step in front stays identical.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import psycopg

from toolwrapper.config import load_config
from toolwrapper.verify import TokenRejected, verify_token

TOOL_NAME = "send_email"


class WrapperServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, port: int, public_key: str, conn: Any) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.public_key = public_key
        self.conn = conn
        self.executions: list[dict[str, Any]] = []

    @property
    def port(self) -> int:
        return self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server: WrapperServer

    def do_POST(self) -> None:
        if self.path != "/execute":
            self._respond(404, {"status": "error", "reason": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
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
            )
        except TokenRejected as exc:
            self._respond(403, {"status": "rejected", "reason": exc.reason})
            return

        if tool_name != TOOL_NAME:
            self._respond(400, {"status": "error", "reason": f"unknown tool: {tool_name}"})
            return

        self.server.executions.append({"tool_name": tool_name, "arguments": arguments})
        result = f"(fake) email sent to {arguments.get('to', '?')}"
        print(f"FAKE TOOL EXECUTED: {tool_name} {json.dumps(arguments)}", flush=True)
        self._respond(200, {"status": "executed", "result": result})

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
    cfg = load_config()
    conn = psycopg.connect(cfg.database_url, autocommit=True)
    server = WrapperServer(cfg.port, cfg.jwt_public_key_path.read_text(), conn)
    print(f"tool wrapper listening on 127.0.0.1:{server.port} (tool: {TOOL_NAME})", flush=True)
    server.serve_forever()

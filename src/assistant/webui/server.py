"""Web UI backend: serves the static client and a small JSON API in front of the
same Orchestrator entry point the CLI uses.

Binds 127.0.0.1 only — in the target topology this sits behind the tunnel, and in
dev nothing is exposed to the network. Single user, one turn at a time (a second
chat while one runs gets 409). Discovery/TOFU runs lazily on the first chat so
approval prompts surface in the UI rather than a terminal nobody is watching.

  GET  /, /app.js, /style.css   static client (allowlist, no directory serving)
  POST /api/chat {message}      run one turn; returns {reply} when it completes
  GET  /api/pending             confirmations/approvals waiting on the user
  POST /api/decision {id,allow} answer one
  GET  /api/persona             read-only persona text
  GET  /api/memory[?q=]         read-only memory browser: recent, or semantic search
  GET  /api/status              {busy, started, tools}
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from assistant.memory.facts import FactStore
from assistant.orchestrator.loop import Orchestrator
from assistant.webui.decisions import DecisionQueue

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class UIServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        port: int,
        *,
        orchestrator: Orchestrator,
        decisions: DecisionQueue,
        persona_text: str,
        browse_store: FactStore,  # its own DB connection — turns run concurrently with browsing
        user_id: str,
    ) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.orchestrator = orchestrator
        self.decisions = decisions
        self.persona_text = persona_text
        self.browse_store = browse_store
        self.user_id = user_id
        self.turn_lock = threading.Lock()
        self.startup_lock = threading.Lock()
        self.started = False
        self.startup_notes: list[str] = []

    @property
    def port(self) -> int:
        return self.server_address[1]

    def ensure_started(self) -> None:
        with self.startup_lock:
            if not self.started:
                self.startup_notes = self.orchestrator.startup()
                self.started = True


class Handler(BaseHTTPRequestHandler):
    server: UIServer

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path in STATIC_FILES:
            filename, content_type = STATIC_FILES[url.path]
            self._respond_raw(200, (STATIC_DIR / filename).read_bytes(), content_type)
        elif url.path == "/api/pending":
            self._respond(200, {"items": self.server.decisions.pending()})
        elif url.path == "/api/persona":
            self._respond(200, {"persona": self.server.persona_text})
        elif url.path == "/api/memory":
            self._memory(parse_qs(url.query))
        elif url.path == "/api/status":
            self._respond(
                200,
                {
                    "busy": self.server.turn_lock.locked(),
                    "started": self.server.started,
                    "tools": self.server.orchestrator.tool_names if self.server.started else [],
                    "notes": self.server.startup_notes,
                },
            )
        else:
            self._respond(404, {"error": "not found"})

    def _memory(self, query: dict[str, list[str]]) -> None:
        store, user = self.server.browse_store, self.server.user_id
        q = (query.get("q") or [""])[0].strip()
        if q:
            facts = store.recall(user, q, k=20, min_similarity=0.0)
            items = [{"content": f.content, "similarity": round(f.similarity, 3)} for f in facts]
        else:
            items = store.browse(user, limit=50)
        self._respond(200, {"items": items})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"error": "invalid JSON body"})
            return

        if self.path == "/api/chat":
            self._chat(body)
        elif self.path == "/api/decision":
            resolved = self.server.decisions.resolve(str(body.get("id", "")), bool(body.get("allow")))
            self._respond(200 if resolved else 404, {"ok": resolved})
        else:
            self._respond(404, {"error": "not found"})

    def _chat(self, body: dict[str, Any]) -> None:
        message = str(body.get("message", "")).strip()
        if not message:
            self._respond(400, {"error": "empty message"})
            return
        if not self.server.turn_lock.acquire(blocking=False):
            self._respond(409, {"error": "a turn is already in progress"})
            return
        try:
            self.server.ensure_started()
            reply = self.server.orchestrator.run_turn(message)
            self._respond(200, {"reply": reply})
        finally:
            self.server.turn_lock.release()

    def _respond(self, code: int, payload: dict[str, Any]) -> None:
        self._respond_raw(code, json.dumps(payload).encode(), "application/json")

    def _respond_raw(self, code: int, data: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def main() -> None:
    import psycopg

    from assistant.config import load_config
    from assistant.memory.embeddings import make_embedder
    from assistant.memory.persona import PersonaLoader
    from assistant.model.claude import ClaudeAdapter
    from assistant.prompt import system_prompt

    cfg = load_config()
    conn = psycopg.connect(cfg.database_url, autocommit=True)
    embedder = make_embedder(cfg.embedding_backend)
    fact_store = FactStore(psycopg.connect(cfg.memory_database_url, autocommit=True), embedder)
    browse_store = FactStore(psycopg.connect(cfg.memory_database_url, autocommit=True), embedder)
    persona = PersonaLoader(cfg.persona_dir).load()
    decisions = DecisionQueue()

    orchestrator = Orchestrator(
        ClaudeAdapter(cfg.anthropic_api_key),
        conn=conn,
        tool_servers=cfg.tool_servers,
        private_key=cfg.jwt_private_key_path.read_text(),
        ttl_seconds=cfg.jwt_ttl_seconds,
        low_ttl_seconds=cfg.jwt_low_tier_ttl_seconds,
        user_id=cfg.user_id,
        system_prompt=system_prompt(persona),
        fact_store=fact_store,
        recall_k=cfg.recall_k,
        recall_min_similarity=cfg.recall_min_similarity,
        confirm=decisions.confirm,
        approve=decisions.approve,
    )
    server = UIServer(
        cfg.ui_port,
        orchestrator=orchestrator,
        decisions=decisions,
        persona_text=persona,
        browse_store=browse_store,
        user_id=cfg.user_id,
    )
    print(f"web ui on http://127.0.0.1:{server.port}  (tool wrappers must be running)", flush=True)
    server.serve_forever()

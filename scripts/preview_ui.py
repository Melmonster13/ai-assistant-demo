"""Preview harness: the real web UI + real wrappers + real memory, with a
scripted model (no API key). 'write <x>' triggers the destructive flow,
'remember <x>' saves a fact, anything else echoes."""

import sys
import threading
from pathlib import Path

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistant.config import ToolServer
from assistant.memory.embeddings import HashingEmbedder
from assistant.memory.facts import FactStore
from assistant.memory.persona import PersonaLoader
from assistant.model.base import ModelResponse, ToolCall
from assistant.orchestrator.loop import Orchestrator
from assistant.webui.decisions import DecisionQueue
from assistant.webui.server import UIServer
from toolwrapper.bridge import McpBridge
from toolwrapper.server import WrapperServer

DB = "postgresql://assistant:assistant@localhost:5433/assistant"
MEM = "postgresql://assistant_app:assistant_app@localhost:5433/assistant"
ROOT = Path.cwd()

key = Ed25519PrivateKey.generate()
private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def wrapper(tier, module, env):
    conn = psycopg.connect(DB, autocommit=True)
    bridge = McpBridge(sys.executable, ["-m", module], env)
    bridge.start()
    server = WrapperServer(0, public, conn, tier=tier, bridge=bridge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Model:
    def __init__(self):
        self.pending_text = None

    def complete(self, messages, tools, system=None):
        if self.pending_text is not None:
            reply, self.pending_text = self.pending_text, None
            return ModelResponse(text=reply, raw_message={"role": "assistant", "content": "x"})
        last = messages[-1]["content"]
        text = last if isinstance(last, str) else str(last)
        low = text.lower()
        if low.startswith("write "):
            self.pending_text = "Wrote it (tool result fed back to me)."
            return ModelResponse(None, [ToolCall("c1", "write_file", {"path": "from-ui.txt", "content": text[6:]})], {"role": "assistant", "content": "x"})
        if low.startswith("remember "):
            self.pending_text = "Saved that to memory."
            return ModelResponse(None, [ToolCall("c2", "remember_fact", {"content": text[9:]})], {"role": "assistant", "content": "x"})
        if low.startswith("read"):
            self.pending_text = "Here are your notes."
            return ModelResponse(None, [ToolCall("c3", "list_notes", {})], {"role": "assistant", "content": "x"})
        return ModelResponse(text=f"(scripted) You said: {text}", raw_message={"role": "assistant", "content": "x"})

    def user_message(self, t):
        return {"role": "user", "content": t}

    def tool_result_message(self, i, c):
        return {"role": "user", "content": c}


notes = wrapper("low", "mcpservers.notes", {"NOTES_DIR": str(ROOT / "data/notes")})
files = wrapper("high", "mcpservers.files", {"FILES_ROOT": str(ROOT / "data/sandbox")})

decisions = DecisionQueue()
embedder = HashingEmbedder()
fact_store = FactStore(psycopg.connect(MEM, autocommit=True), embedder)
browse_store = FactStore(psycopg.connect(MEM, autocommit=True), embedder)
persona = PersonaLoader(ROOT / "data/persona").load()

orchestrator = Orchestrator(
    Model(),
    conn=psycopg.connect(DB, autocommit=True),
    tool_servers=(
        ToolServer("notes", f"http://127.0.0.1:{notes.port}", "low"),
        ToolServer("files", f"http://127.0.0.1:{files.port}", "high"),
    ),
    private_key=private,
    ttl_seconds=30,
    low_ttl_seconds=900,
    user_id="preview",
    system_prompt="base",
    fact_store=fact_store,
    recall_min_similarity=0.0,
    confirm=decisions.confirm,
    approve=decisions.approve,
)
ui = UIServer(8090, orchestrator=orchestrator, decisions=decisions, persona_text=persona, browse_store=browse_store, user_id="preview")
print(f"preview ui on http://127.0.0.1:{ui.port}", flush=True)
ui.serve_forever()

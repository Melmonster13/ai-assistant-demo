"""Web UI: the decision queue fails closed, and the API drives the same
orchestrator gate end-to-end — a destructive call surfaces as a pending
confirmation card, Allow executes it, Deny blocks it, browsers are read-only."""

import threading
import time
import uuid

import httpx
import pytest

from assistant.config import ToolServer
from assistant.memory.embeddings import HashingEmbedder
from assistant.memory.facts import FactStore
from assistant.model.base import ModelResponse, ToolCall
from assistant.orchestrator.loop import Orchestrator
from assistant.webui.decisions import DecisionQueue
from assistant.webui.server import UIServer


# --- decision queue -------------------------------------------------------


def test_queue_allow_and_deny():
    queue = DecisionQueue()
    results = {}

    def worker(name):
        results[name] = queue.confirm(name, {"x": 1})

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("t1", "t2")]
    for t in threads:
        t.start()
    deadline = time.time() + 5
    while len(queue.pending()) < 2 and time.time() < deadline:
        time.sleep(0.01)
    items = {i["tool_name"]: i["id"] for i in queue.pending()}
    assert queue.resolve(items["t1"], True)
    assert queue.resolve(items["t2"], False)
    for t in threads:
        t.join(timeout=5)
    assert results == {"t1": True, "t2": False}


def test_queue_timeout_denies():
    queue = DecisionQueue(timeout_seconds=0.05)
    assert queue.confirm("tool", {}) is False  # nobody answered -> deny


def test_queue_unknown_and_double_resolve():
    queue = DecisionQueue(timeout_seconds=2)
    assert queue.resolve("nope", True) is False
    done = []
    t = threading.Thread(target=lambda: done.append(queue.confirm("t", {})))
    t.start()
    while not queue.pending():
        time.sleep(0.01)
    item_id = queue.pending()[0]["id"]
    assert queue.resolve(item_id, True) is True
    assert queue.resolve(item_id, False) is False  # already decided
    t.join(timeout=5)
    assert done == [True]


# --- API end-to-end --------------------------------------------------------


class ScriptedModel:
    """One destructive tool call, then a text reply."""

    def __init__(self):
        self.step = 0

    def complete(self, messages, tools, system=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                text=None,
                tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "ui.txt", "content": "from ui"})],
                raw_message={"role": "assistant", "content": "[tc]"},
            )
        return ModelResponse(text=f"done: {messages[-1]['content']}", raw_message={"role": "assistant", "content": "[t]"})

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_message(self, tool_call_id, content):
        return {"role": "user", "content": content}


@pytest.fixture()
def ui(db_conn, memory_conn, files_wrapper, keypair):
    user = f"ui-{uuid.uuid4().hex[:8]}"
    decisions = DecisionQueue(timeout_seconds=30)
    fact_store = FactStore(memory_conn, HashingEmbedder())
    orchestrator = Orchestrator(
        ScriptedModel(),
        conn=db_conn,
        tool_servers=(ToolServer("files", f"http://127.0.0.1:{files_wrapper.port}", "high"),),
        private_key=keypair.private,
        ttl_seconds=30,
        low_ttl_seconds=900,
        user_id=user,
        system_prompt="base",
        fact_store=fact_store,
        confirm=decisions.confirm,
        approve=lambda prompt: True,  # auto-TOFU; approval cards are exercised by the queue tests
    )
    server = UIServer(
        0,
        orchestrator=orchestrator,
        decisions=decisions,
        persona_text="# Persona\ncalm and dry",
        browse_store=fact_store,
        user_id=user,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.base = f"http://127.0.0.1:{server.port}"
    server.fact_store = fact_store
    server.user = user
    yield server
    server.shutdown()


def _chat_async(base: str, message: str) -> list:
    box = []

    def run():
        box.append(httpx.post(f"{base}/api/chat", json={"message": message}, timeout=60))

    threading.Thread(target=run, daemon=True).start()
    return box


def _wait_pending(base: str, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = httpx.get(f"{base}/api/pending", timeout=5).json()["items"]
        confirms = [i for i in items if i["kind"] == "confirm"]
        if confirms:
            return confirms[0]
        time.sleep(0.05)
    raise AssertionError("no pending confirmation appeared")


def test_chat_confirmation_allow_flow(ui, sandbox):
    box = _chat_async(ui.base, "write the file please")
    item = _wait_pending(ui.base)
    assert item["tool_name"] == "write_file"
    assert item["arguments"]["path"] == "ui.txt"

    allowed = httpx.post(f"{ui.base}/api/decision", json={"id": item["id"], "allow": True}, timeout=5)
    assert allowed.json()["ok"] is True

    deadline = time.time() + 15
    while not box and time.time() < deadline:
        time.sleep(0.05)
    assert box and box[0].status_code == 200
    assert "wrote" in box[0].json()["reply"]
    assert (sandbox / "ui.txt").read_text() == "from ui"


def test_chat_confirmation_deny_flow(ui, sandbox):
    box = _chat_async(ui.base, "write the file please")
    item = _wait_pending(ui.base)
    httpx.post(f"{ui.base}/api/decision", json={"id": item["id"], "allow": False}, timeout=5)

    deadline = time.time() + 15
    while not box and time.time() < deadline:
        time.sleep(0.05)
    assert box and box[0].status_code == 200
    assert "denied" in box[0].json()["reply"]
    assert not (sandbox / "ui.txt").exists()


def test_persona_and_memory_endpoints(ui):
    persona = httpx.get(f"{ui.base}/api/persona", timeout=5).json()
    assert "calm and dry" in persona["persona"]

    ui.fact_store.remember(ui.user, "The user's bike is a red gravel bike.")
    recent = httpx.get(f"{ui.base}/api/memory", timeout=5).json()["items"]
    assert any("gravel" in i["content"] for i in recent)
    assert "created_at" in recent[0]

    search = httpx.get(f"{ui.base}/api/memory", params={"q": "bike"}, timeout=5).json()["items"]
    assert any("gravel" in i["content"] for i in search)
    assert "similarity" in search[0]


def test_static_and_status(ui):
    page = httpx.get(f"{ui.base}/", timeout=5)
    assert page.status_code == 200 and "<title>assistant</title>" in page.text
    for path in ("/app.js", "/style.css"):
        assert httpx.get(f"{ui.base}{path}", timeout=5).status_code == 200
    assert httpx.get(f"{ui.base}/api/nope", timeout=5).status_code == 404
    status = httpx.get(f"{ui.base}/api/status", timeout=5).json()
    assert status["busy"] is False

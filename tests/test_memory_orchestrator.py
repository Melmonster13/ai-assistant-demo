"""Memory through the orchestrator: persona + auto-recalled facts reach the model
via the system prompt, and remember_fact is a direct integration — no wrapper, no
token, no confirmation, but audited."""

import uuid

import pytest

from assistant.memory.embeddings import HashingEmbedder
from assistant.memory.facts import FactStore
from assistant.model.base import ModelResponse, ToolCall
from assistant.orchestrator.loop import Orchestrator


class CaptureModel:
    """Records the system prompt it was called with; optionally emits one tool
    call on the first step, then a text reply."""

    def __init__(self, tool_calls=None):
        self.systems: list[str] = []
        self._tool_calls = tool_calls or []
        self.step = 0

    def complete(self, messages, tools, system=None):
        self.systems.append(system or "")
        self.step += 1
        if self.step == 1 and self._tool_calls:
            return ModelResponse(text=None, tool_calls=self._tool_calls, raw_message={"role": "assistant", "content": "[tc]"})
        return ModelResponse(text="ok", raw_message={"role": "assistant", "content": "[t]"})

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_message(self, tool_call_id, content):
        return {"role": "user", "content": content}


@pytest.fixture()
def user() -> str:
    return f"u-{uuid.uuid4().hex[:8]}"


def _orchestrator(model, conn, fact_store, user, **kw):
    kw.setdefault("recall_min_similarity", 0.0)
    return Orchestrator(
        model,
        conn=conn,
        tool_servers=(),
        private_key="unused",
        ttl_seconds=30,
        low_ttl_seconds=900,
        user_id=user,
        system_prompt="BASE PERSONA PROMPT",
        fact_store=fact_store,
        confirm=lambda n, a: pytest.fail("memory writes must not prompt for confirmation"),
        **kw,
    )


def test_persona_and_recall_reach_system_prompt(db_conn, memory_conn, user):
    fact_store = FactStore(memory_conn, HashingEmbedder())
    fact_store.remember(user, "The user prefers metric units.")
    model = CaptureModel()
    orch = _orchestrator(model, db_conn, fact_store, user)

    orch.run_turn("remind me about units please")

    system = model.systems[0]
    assert "BASE PERSONA PROMPT" in system  # persona carried through
    assert "metric units" in system  # fact auto-recalled into context


def test_no_recall_block_when_nothing_relevant(db_conn, memory_conn, user):
    fact_store = FactStore(memory_conn, HashingEmbedder())
    model = CaptureModel()
    orch = _orchestrator(model, db_conn, fact_store, user, recall_min_similarity=0.99)

    orch.run_turn("hello")
    assert model.systems[0] == "BASE PERSONA PROMPT"  # no empty "you remember" block


def test_remember_fact_is_direct_and_audited(db_conn, memory_conn, user):
    fact_store = FactStore(memory_conn, HashingEmbedder())
    model = CaptureModel(
        tool_calls=[ToolCall(id="c1", name="remember_fact", arguments={"content": "The user is vegetarian."})]
    )
    orch = _orchestrator(model, db_conn, fact_store, user)

    reply = orch.run_turn("just so you know, I'm vegetarian")
    assert reply == "ok"

    # persisted and recallable
    facts = [f.content for f in fact_store.recall(user, "diet", k=5, min_similarity=0.0)]
    assert "The user is vegetarian." in facts

    # audited as a plain tool call, with no token ever minted for it
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT event FROM audit_log WHERE user_id = %s AND tool_name = 'remember_fact' ORDER BY id",
            (user,),
        )
        events = [r[0] for r in cur.fetchall()]
    assert events == ["requested", "executed"]  # no confirmed / token_minted


def test_remember_fact_exposed_as_tool(db_conn, memory_conn, user):
    fact_store = FactStore(memory_conn, HashingEmbedder())
    orch = _orchestrator(CaptureModel(), db_conn, fact_store, user)
    assert any(t["name"] == "remember_fact" for t in orch._tools)

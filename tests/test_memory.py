"""Fact-recall memory: semantic ranking, RLS user isolation (the "user_id-keyed
from day one" guarantee, enforced not just filtered), and the persona loader."""

import uuid

import psycopg
import pytest

from assistant.memory.persona import PersonaLoader


@pytest.fixture()
def user() -> str:
    return f"u-{uuid.uuid4().hex[:8]}"


def test_remember_and_semantic_recall(fact_store, user):
    fact_store.remember(user, "The user's dog is a border collie named Pixel.")
    fact_store.remember(user, "The user takes their coffee black, no sugar.")
    fact_store.remember(user, "The user's flight to Lisbon is on the 14th.")

    facts = fact_store.recall(user, "what kind of dog do they have", k=1)
    assert len(facts) == 1
    assert "border collie" in facts[0].content


def test_recall_is_empty_for_new_user(fact_store, user):
    assert fact_store.recall(user, "anything", k=5) == []


def test_rls_isolates_users(fact_store):
    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
    fact_store.remember(alice, "Alice's password hint is her first pet.")
    fact_store.remember(bob, "Bob's favorite color is green.")

    # each user's recall only ever sees their own rows
    alice_facts = [f.content for f in fact_store.recall(alice, "secret", k=10)]
    bob_facts = [f.content for f in fact_store.recall(bob, "secret", k=10)]
    assert any("Alice" in c for c in alice_facts)
    assert all("Bob" not in c for c in alice_facts)
    assert any("Bob" in c for c in bob_facts)
    assert all("Alice" not in c for c in bob_facts)


def test_rls_blocks_cross_user_read_structurally(memory_conn):
    """Even a raw SELECT with no user_id filter returns only the scoped user's
    rows — the isolation is in Postgres, not the query."""
    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
    from assistant.memory.embeddings import HashingEmbedder
    from assistant.memory.facts import FactStore

    store = FactStore(memory_conn, HashingEmbedder())
    store.remember(alice, "alice-only row")
    store.remember(bob, "bob-only row")

    with memory_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_user_id', %s, false)", (alice,))
        cur.execute("SELECT content FROM facts")  # no WHERE clause on purpose
        visible = [r[0] for r in cur.fetchall()]
    assert "alice-only row" in visible
    assert "bob-only row" not in visible


def test_memory_role_is_not_superuser(memory_conn):
    with memory_conn.cursor() as cur:
        cur.execute("SELECT current_user, current_setting('is_superuser')")
        role, is_super = cur.fetchone()
    assert role == "assistant_app"
    assert is_super == "off"  # or RLS would be bypassed


def test_persona_loader_concatenates(tmp_path):
    (tmp_path / "01_intro.md").write_text("# Intro\nCalm and dry.\n")
    (tmp_path / "02_style.md").write_text("Short answers.\n")
    (tmp_path / "notes.txt").write_text("ignored, not markdown")
    persona = PersonaLoader(tmp_path).load()
    assert "Calm and dry." in persona
    assert "Short answers." in persona
    assert "ignored" not in persona
    assert persona.index("Intro") < persona.index("Short answers")  # sorted order


def test_persona_loader_absent_dir_is_empty(tmp_path):
    assert PersonaLoader(tmp_path / "nope").load() == ""

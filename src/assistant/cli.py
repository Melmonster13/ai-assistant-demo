"""Text-chat entry point. UI (Phase 4) and voice (Phase 5) later terminate at
the same Orchestrator entry point."""

import psycopg

from assistant.config import load_config
from assistant.memory.embeddings import make_embedder
from assistant.memory.facts import FactStore
from assistant.memory.persona import PersonaLoader
from assistant.model.claude import ClaudeAdapter
from assistant.orchestrator.loop import Orchestrator

BASE_INSTRUCTIONS = (
    "You are a personal assistant. Use the available tools when the request "
    "calls for them. Destructive actions are confirmed with the user before "
    "they execute; if a tool call is denied or rejected, accept that and do "
    "not retry it unasked. When the user shares something worth remembering, "
    "use remember_fact."
)


def _system_prompt(persona: str) -> str:
    if not persona:
        return BASE_INSTRUCTIONS
    return f"{BASE_INSTRUCTIONS}\n\nWho you are and who you're talking to:\n{persona}"


def main() -> None:
    cfg = load_config()
    conn = psycopg.connect(cfg.database_url, autocommit=True)
    # separate non-superuser connection so RLS on facts is enforced, not bypassed
    memory_conn = psycopg.connect(cfg.memory_database_url, autocommit=True)
    fact_store = FactStore(memory_conn, make_embedder(cfg.embedding_backend))
    persona = PersonaLoader(cfg.persona_dir).load()

    orchestrator = Orchestrator(
        ClaudeAdapter(cfg.anthropic_api_key),
        conn=conn,
        tool_servers=cfg.tool_servers,
        private_key=cfg.jwt_private_key_path.read_text(),
        ttl_seconds=cfg.jwt_ttl_seconds,
        low_ttl_seconds=cfg.jwt_low_tier_ttl_seconds,
        user_id=cfg.user_id,
        system_prompt=_system_prompt(persona),
        fact_store=fact_store,
        recall_k=cfg.recall_k,
        recall_min_similarity=cfg.recall_min_similarity,
    )
    print("assistant — Phase 3: memory. 'exit' or Ctrl-D to quit.")
    print("(run `tool-wrapper notes` and `tool-wrapper files` in other terminals first)")
    if persona:
        print(f"persona: loaded from {cfg.persona_dir}")
    for note in orchestrator.startup():
        print(note)
    while True:
        try:
            user_input = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input in {"exit", "quit"}:
            break
        if not user_input:
            continue
        print(f"\nassistant> {orchestrator.run_turn(user_input)}")

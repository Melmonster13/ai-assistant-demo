"""Text-chat entry point. UI (Phase 4) and voice (Phase 5) later terminate at
the same Orchestrator entry point."""

import psycopg

from assistant.config import load_config
from assistant.model.claude import ClaudeAdapter
from assistant.orchestrator.loop import Orchestrator

SYSTEM_PROMPT = (
    "You are a personal assistant. Use the available tools when the request "
    "calls for them. Destructive actions are confirmed with the user before "
    "they execute; if a tool call is denied or rejected, accept that and do "
    "not retry it unasked."
)


def main() -> None:
    cfg = load_config()
    conn = psycopg.connect(cfg.database_url, autocommit=True)
    orchestrator = Orchestrator(
        ClaudeAdapter(cfg.anthropic_api_key),
        conn=conn,
        tool_servers=cfg.tool_servers,
        private_key=cfg.jwt_private_key_path.read_text(),
        ttl_seconds=cfg.jwt_ttl_seconds,
        low_ttl_seconds=cfg.jwt_low_tier_ttl_seconds,
        user_id=cfg.user_id,
        system_prompt=SYSTEM_PROMPT,
    )
    print("assistant — Phase 2: real tools via MCP. 'exit' or Ctrl-D to quit.")
    print("(run `tool-wrapper notes` and `tool-wrapper files` in other terminals first)")
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

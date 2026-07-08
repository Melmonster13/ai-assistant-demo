"""Text-chat entry point for Phase 1. UI (Phase 4) and voice (Phase 5) later
terminate at the same Orchestrator entry point."""

import psycopg

from assistant.config import load_config
from assistant.model.claude import ClaudeAdapter
from assistant.orchestrator.loop import Orchestrator

FAKE_TOOL = {
    "name": "send_email",
    "description": (
        "Send an email on the user's behalf. Destructive action: "
        "requires explicit user confirmation before it runs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body"},
        },
        "required": ["to", "body"],
    },
}

SYSTEM_PROMPT = (
    "You are a personal assistant. You can send email with the send_email tool. "
    "Destructive actions are confirmed with the user before they execute; if a "
    "tool call is denied or rejected, accept that and do not retry it unasked."
)


def main() -> None:
    cfg = load_config()
    conn = psycopg.connect(cfg.database_url, autocommit=True)
    orchestrator = Orchestrator(
        ClaudeAdapter(cfg.anthropic_api_key),
        conn=conn,
        tools=[FAKE_TOOL],
        wrapper_url=cfg.tool_wrapper_url,
        private_key=cfg.jwt_private_key_path.read_text(),
        ttl_seconds=cfg.jwt_ttl_seconds,
        user_id=cfg.user_id,
    )
    print("assistant — Phase 1 security spine. 'exit' or Ctrl-D to quit.")
    print("(run `fake-tool-server` in another terminal first)")
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

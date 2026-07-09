"""System prompt assembly, shared by every interface (CLI, web UI, voice later —
all equal peers over the same orchestrator)."""

BASE_INSTRUCTIONS = (
    "You are a personal assistant. Use the available tools when the request "
    "calls for them. Destructive actions are confirmed with the user before "
    "they execute; if a tool call is denied or rejected, accept that and do "
    "not retry it unasked. When the user shares something worth remembering, "
    "use remember_fact."
)


def system_prompt(persona: str) -> str:
    if not persona:
        return BASE_INSTRUCTIONS
    return f"{BASE_INSTRUCTIONS}\n\nWho you are and who you're talking to:\n{persona}"

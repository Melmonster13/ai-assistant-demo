"""CLI y/n confirmation for destructive actions. Replaced by UI buttons in Phase 4;
the gate itself stays at the orchestrator level either way."""

import json
from typing import Any


def confirm(tool_name: str, arguments: dict[str, Any]) -> bool:
    print(f"\nTool wants to run: {tool_name}")
    print(json.dumps(arguments, indent=2))
    return input("Allow? [y/N] ").strip().lower() == "y"


def approve(prompt: str) -> bool:
    """One-time approval prompt (TOFU baseline / definition drift), reusing the
    same CLI surface as per-call confirmation."""
    print(f"\n{prompt}")
    return input("Approve? [y/N] ").strip().lower() == "y"

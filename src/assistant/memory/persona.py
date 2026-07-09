"""Persona/profile loader: hand-authored markdown that shapes the assistant's
tone and personality, injected into the model's system prompt. Read-only, direct
integration. In dev the "vault" is just a local folder; Phase 6 adds one-way sync
from the authoring machine — the loader doesn't change, only where the folder is.
"""

from pathlib import Path


class PersonaLoader:
    def __init__(self, persona_dir: Path) -> None:
        self._dir = persona_dir

    def load(self) -> str:
        """Concatenate all markdown in the folder (sorted for stable ordering).
        Returns "" if the folder is absent or empty — persona is optional."""
        if not self._dir.is_dir():
            return ""
        parts = []
        for path in sorted(self._dir.glob("*.md")):
            text = path.read_text().strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)

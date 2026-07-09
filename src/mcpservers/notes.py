"""Read-only notes MCP server — the Phase 2 low-risk-tier tool. Serves markdown
files from NOTES_DIR; foreshadows the Phase 3 persona folder."""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("notes")


def _notes_dir() -> Path:
    return Path(os.environ.get("NOTES_DIR", "data/notes")).resolve()


@mcp.tool()
def list_notes() -> str:
    """List the filenames of all saved notes."""
    names = sorted(p.name for p in _notes_dir().glob("*.md"))
    return "\n".join(names) or "(no notes)"


@mcp.tool()
def read_note(name: str) -> str:
    """Read a note by filename, as returned by list_notes."""
    notes_dir = _notes_dir()
    path = (notes_dir / name).resolve()
    if path.parent != notes_dir or path.suffix != ".md":
        return "error: invalid note name"
    if not path.is_file():
        return f"error: no note named {name}"
    return path.read_text()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

"""Destructive files MCP server — the Phase 2 high-tier tool. Writes and deletes
real files, confined to FILES_ROOT."""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("files")


def _resolve(path: str) -> Path | None:
    root = Path(os.environ["FILES_ROOT"]).resolve()
    target = (root / path).resolve()
    return target if target.is_relative_to(root) and target != root else None


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file. Path is relative to the sandbox root."""
    target = _resolve(path)
    if target is None:
        return "error: path escapes the sandbox root"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {len(content)} chars to {path}"


@mcp.tool()
def delete_file(path: str) -> str:
    """Delete a file. Path is relative to the sandbox root."""
    target = _resolve(path)
    if target is None:
        return "error: path escapes the sandbox root"
    if not target.is_file():
        return f"error: no file at {path}"
    target.unlink()
    return f"deleted {path}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

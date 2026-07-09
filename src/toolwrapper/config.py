"""Wrapper-side config, separate from assistant.config on purpose — this package
must not import orchestrator internals. It holds only the public key (verify,
never mint) and DB access for the jti table.

servers.toml is shared *data* between the two packages (which wrapper fronts
which MCP server, on which port, at which tier), not shared code."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class ServerSpec:
    server_id: str
    port: int
    tier: str  # "low" | "high"
    module: str
    env_keys: tuple[str, ...]


@dataclass(frozen=True)
class WrapperConfig:
    database_url: str
    jwt_public_key_path: Path
    server: ServerSpec


def load_server_specs(path: Path | None = None) -> dict[str, ServerSpec]:
    path = path or Path(os.environ.get("SERVERS_CONFIG", "servers.toml"))
    data = tomllib.loads(path.read_text())
    specs = {}
    for server_id, entry in data["servers"].items():
        if entry["tier"] not in ("low", "high"):
            raise ValueError(f"{server_id}: tier must be 'low' or 'high'")
        specs[server_id] = ServerSpec(
            server_id=server_id,
            port=entry["port"],
            tier=entry["tier"],
            module=entry["module"],
            env_keys=tuple(entry.get("env", [])),
        )
    return specs


def load_config(server_id: str) -> WrapperConfig:
    load_dotenv()
    specs = load_server_specs()
    if server_id not in specs:
        raise SystemExit(f"unknown server '{server_id}' — known: {', '.join(specs)}")
    return WrapperConfig(
        database_url=os.environ["DATABASE_URL"],
        jwt_public_key_path=Path(os.environ["JWT_PUBLIC_KEY_PATH"]),
        server=specs[server_id],
    )

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class ToolServer:
    server_id: str
    url: str
    tier: str  # "low" | "high"


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    database_url: str
    memory_database_url: str
    jwt_private_key_path: Path
    jwt_ttl_seconds: int
    jwt_low_tier_ttl_seconds: int
    user_id: str
    tool_servers: tuple[ToolServer, ...]
    persona_dir: Path
    embedding_backend: str
    recall_k: int
    recall_min_similarity: float
    ui_port: int


def _load_tool_servers() -> tuple[ToolServer, ...]:
    # servers.toml is shared data with toolwrapper (which wrapper is where, at
    # which tier), parsed independently — the packages stay import-isolated
    path = Path(os.environ.get("SERVERS_CONFIG", "servers.toml"))
    data = tomllib.loads(path.read_text())
    return tuple(
        ToolServer(server_id=sid, url=f"http://127.0.0.1:{entry['port']}", tier=entry["tier"])
        for sid, entry in data["servers"].items()
    )


def load_config() -> Config:
    load_dotenv()
    return Config(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        database_url=os.environ["DATABASE_URL"],
        # non-superuser role so RLS on facts actually enforces
        memory_database_url=os.environ["MEMORY_DATABASE_URL"],
        jwt_private_key_path=Path(os.environ["JWT_PRIVATE_KEY_PATH"]),
        jwt_ttl_seconds=int(os.environ.get("JWT_TTL_SECONDS", "30")),
        jwt_low_tier_ttl_seconds=int(os.environ.get("JWT_LOW_TIER_TTL_SECONDS", "900")),
        user_id=os.environ.get("USER_ID", "user"),
        tool_servers=_load_tool_servers(),
        persona_dir=Path(os.environ.get("PERSONA_DIR", "data/persona")),
        embedding_backend=os.environ.get("EMBEDDING_BACKEND", "local"),
        recall_k=int(os.environ.get("RECALL_K", "5")),
        recall_min_similarity=float(os.environ.get("RECALL_MIN_SIMILARITY", "0.25")),
        ui_port=int(os.environ.get("UI_PORT", "8080")),
    )

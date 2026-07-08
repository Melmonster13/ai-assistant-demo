"""Wrapper-side config, separate from assistant.config on purpose — this package
must not import orchestrator internals. It holds only the public key (verify,
never mint) and DB access for the jti table."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class WrapperConfig:
    database_url: str
    jwt_public_key_path: Path
    port: int


def load_config() -> WrapperConfig:
    load_dotenv()
    return WrapperConfig(
        database_url=os.environ["DATABASE_URL"],
        jwt_public_key_path=Path(os.environ["JWT_PUBLIC_KEY_PATH"]),
        port=int(os.environ.get("TOOL_WRAPPER_PORT", "8100")),
    )

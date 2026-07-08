import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    database_url: str
    tool_wrapper_url: str
    jwt_private_key_path: Path
    jwt_ttl_seconds: int
    user_id: str


def load_config() -> Config:
    load_dotenv()
    return Config(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        database_url=os.environ["DATABASE_URL"],
        tool_wrapper_url=os.environ["TOOL_WRAPPER_URL"],
        jwt_private_key_path=Path(os.environ["JWT_PRIVATE_KEY_PATH"]),
        jwt_ttl_seconds=int(os.environ.get("JWT_TTL_SECONDS", "30")),
        user_id=os.environ.get("USER_ID", "user"),
    )

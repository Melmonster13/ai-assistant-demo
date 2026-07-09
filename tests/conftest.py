import os
import sys
import threading
from typing import Any, NamedTuple

import httpx
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistant.guardrails import broker
from toolwrapper.bridge import McpBridge
from toolwrapper.server import WrapperServer

DB_URL = os.environ.get("DATABASE_URL", "postgresql://assistant:assistant@localhost:5433/assistant")

FILES_ARGS = {"path": "a.txt", "content": "hi"}


class Keypair(NamedTuple):
    private: str
    public: str


def _generate_keypair() -> Keypair:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return Keypair(private, public)


@pytest.fixture(scope="session")
def keypair() -> Keypair:
    return _generate_keypair()


@pytest.fixture(scope="session")
def forged_keypair() -> Keypair:
    return _generate_keypair()


def _connect() -> psycopg.Connection:
    try:
        return psycopg.connect(DB_URL, autocommit=True, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable — run `docker compose up -d` first")


@pytest.fixture(scope="session")
def db_conn():
    conn = _connect()
    yield conn
    conn.close()


def _start_wrapper(keypair: Keypair, tier: str, module: str, env: dict[str, str]):
    conn = _connect()
    bridge = McpBridge(sys.executable, ["-m", module], env)
    bridge.start()
    server = WrapperServer(0, keypair.public, conn, tier=tier, bridge=bridge)  # port 0 = ephemeral
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, bridge, conn


@pytest.fixture()
def sandbox(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    return root


@pytest.fixture()
def files_wrapper(keypair: Keypair, sandbox):
    server, bridge, conn = _start_wrapper(keypair, "high", "mcpservers.files", {"FILES_ROOT": str(sandbox)})
    yield server
    server.shutdown()
    bridge.stop()
    conn.close()


@pytest.fixture()
def notes_dir(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    (d / "welcome.md").write_text("# Welcome\n")
    (d / "todo.md").write_text("# Todo\n- tests\n")
    return d


@pytest.fixture()
def notes_wrapper(keypair: Keypair, notes_dir):
    server, bridge, conn = _start_wrapper(keypair, "low", "mcpservers.notes", {"NOTES_DIR": str(notes_dir)})
    yield server
    server.shutdown()
    bridge.stop()
    conn.close()


@pytest.fixture()
def mint(keypair: Keypair, db_conn):
    def _mint(
        tool_name: str = "write_file",
        arguments: dict[str, Any] | None = None,
        ttl_seconds: int = 30,
        private_key: str | None = None,
        tier: str = "high",
    ) -> broker.MintedToken:
        return broker.mint_token(
            tool_name,
            arguments if arguments is not None else FILES_ARGS,
            "test-user",
            private_key=private_key or keypair.private,
            ttl_seconds=ttl_seconds,
            conn=db_conn,
            tier=tier,
        )

    return _mint


@pytest.fixture()
def post():
    def _post(
        wrapper: WrapperServer,
        token: str | None,
        tool_name: str = "write_file",
        arguments: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return httpx.post(
            f"http://127.0.0.1:{wrapper.port}/execute",
            json={
                "tool_name": tool_name,
                "arguments": arguments if arguments is not None else FILES_ARGS,
                "token": token,
            },
            timeout=15,
        )

    return _post

import os
import threading
from typing import Any, NamedTuple

import httpx
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistant.guardrails import broker
from toolwrapper.server import WrapperServer

DB_URL = os.environ.get("DATABASE_URL", "postgresql://assistant:assistant@localhost:5433/assistant")


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


@pytest.fixture()
def wrapper(keypair: Keypair):
    conn = _connect()
    server = WrapperServer(0, keypair.public, conn)  # port 0 = ephemeral
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    conn.close()


@pytest.fixture()
def mint(keypair: Keypair, db_conn):
    def _mint(
        tool_name: str = "send_email",
        arguments: dict[str, Any] | None = None,
        ttl_seconds: int = 30,
        private_key: str | None = None,
    ) -> broker.MintedToken:
        return broker.mint_token(
            tool_name,
            arguments if arguments is not None else {"to": "a@b.c", "body": "hi"},
            "test-user",
            private_key=private_key or keypair.private,
            ttl_seconds=ttl_seconds,
            conn=db_conn,
        )

    return _mint


@pytest.fixture()
def execute(wrapper: WrapperServer):
    def _execute(
        token: str | None,
        tool_name: str = "send_email",
        arguments: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return httpx.post(
            f"http://127.0.0.1:{wrapper.port}/execute",
            json={
                "tool_name": tool_name,
                "arguments": arguments if arguments is not None else {"to": "a@b.c", "body": "hi"},
                "token": token,
            },
            timeout=5,
        )

    return _execute

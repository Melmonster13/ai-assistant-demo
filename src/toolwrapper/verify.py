"""Token verification at the tool boundary — the gate the orchestrator cannot skip.

Everything is checked against the public key only; this process cannot mint.
All DB connections are expected to be autocommit.
"""

import hashlib
import json
import logging
from contextlib import nullcontext
from typing import Any

import jwt

logger = logging.getLogger(__name__)


class TokenRejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def hash_arguments(arguments: dict[str, Any]) -> str:
    # Protocol shared with assistant.guardrails.broker.hash_arguments — duplicated,
    # not imported, so this package stays independent of orchestrator code.
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def verify_token(
    token: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    public_key: str,
    conn: Any,
    tier: str,
    db_lock: Any = None,
) -> dict[str, Any]:
    """Return the verified claims, or raise TokenRejected.

    `tier` is this boundary's own configured tier — enforcement lives here, not
    in the token: a high-tier wrapper demands argument binding and single-use
    (jti consumed atomically, so one token can never authorize two executions)
    no matter what the orchestrator minted. Low tier still requires a valid
    signed, unexpired, tool-bound token, but allows multi-use within its TTL.

    `db_lock` (optional) serializes the jti-consuming UPDATE across request
    threads. Correctness for single-use rests on Postgres row-locking; the lock
    only makes that guarantee independent of the shared connection's threading
    semantics under a threaded server.

    Low-tier revocation trade-off: low-tier verification is deliberately
    DB-free — it never touches the jti table, so a leaked low-tier token stays
    valid until `exp` with no way to kill it early. This is the read-cheap side
    of the tier split (see JIT tiering rationale); the blast radius of a leak is
    read-only tool access bounded by JWT_LOW_TIER_TTL_SECONDS. Killing a
    low-tier token early would require a per-call DB lookup on every read.
    """
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["EdDSA"],
            options={"require": ["exp", "iat", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        raise TokenRejected("token expired") from None
    except jwt.InvalidTokenError as exc:
        # log the PyJWT detail server-side; return a generic reason to the caller
        logger.warning("token rejected for %s: invalid token: %s", tool_name, exc)
        raise TokenRejected("invalid token") from None

    if claims.get("tier") != tier:
        raise TokenRejected(f"token tier does not match this tool boundary (expected {tier})")
    if claims.get("tool_name") != tool_name:
        raise TokenRejected("token was minted for a different tool")

    if tier == "high":
        if claims.get("arguments_hash") != hash_arguments(arguments):
            raise TokenRejected("arguments differ from what was confirmed")
        with (db_lock or nullcontext()), conn.cursor() as cur:
            cur.execute(
                "UPDATE jti SET used_at = now() WHERE jti = %s AND used_at IS NULL",
                (claims["jti"],),
            )
            if cur.rowcount != 1:
                raise TokenRejected("token unknown or already used")

    return claims

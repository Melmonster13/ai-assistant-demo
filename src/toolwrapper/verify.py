"""Token verification at the tool boundary — the gate the orchestrator cannot skip.

Everything is checked against the public key only; this process cannot mint.
All DB connections are expected to be autocommit.
"""

import hashlib
import json
from typing import Any

import jwt


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
) -> dict[str, Any]:
    """Return the verified claims, or raise TokenRejected.

    Checks: signature + expiry, tool binding, argument binding, then single-use —
    the jti row is consumed atomically so a token can never authorize two
    executions, even under races.
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
        raise TokenRejected(f"invalid token: {exc}") from None

    if claims.get("tool_name") != tool_name:
        raise TokenRejected("token was minted for a different tool")
    if claims.get("arguments_hash") != hash_arguments(arguments):
        raise TokenRejected("arguments differ from what was confirmed")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jti SET used_at = now() WHERE jti = %s AND used_at IS NULL",
            (claims["jti"],),
        )
        if cur.rowcount != 1:
            raise TokenRejected("token unknown or already used")

    return claims

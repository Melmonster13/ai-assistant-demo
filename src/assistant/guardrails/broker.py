"""JIT token broker: mints short-TTL, single-use JWTs after confirmation.

Signing is asymmetric (Ed25519/EdDSA) on purpose — this side holds the private
key, tool wrappers hold only the public key. Verification happens at the tool
boundary (toolwrapper.verify), never here: a token this process both minted and
checked would be a promise to itself, not a gate.
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

import jwt


class MintedToken(NamedTuple):
    token: str
    jti: str


def hash_arguments(arguments: dict[str, Any]) -> str:
    # Protocol shared with toolwrapper.verify.hash_arguments — duplicated, not
    # imported, so the two packages stay independent.
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def mint_token(
    tool_name: str,
    arguments: dict[str, Any],
    user_id: str,
    *,
    private_key: str,
    ttl_seconds: int,
    conn: Any,
    tier: str = "high",
) -> MintedToken:
    """Mint a JWT permission slip for `tool_name`, tiered by risk.

    tier "high": single-use, argument-bound, minted only after confirmation —
    authorizes exactly one call with exactly these arguments.
    tier "low": longer-lived and multi-use within its TTL, bound to the tool but
    not to arguments (read-only tools take varying arguments across calls).
    The wrapper's own configured tier decides which checks it enforces; a claim
    can't talk a high-tier boundary out of them.
    """
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    token_id = str(uuid.uuid4())
    claims = {
        "jti": token_id,
        "sub": user_id,
        "tool_name": tool_name,
        "tier": tier,
        "iat": now,
        "exp": expires,
    }
    if tier == "high":
        claims["arguments_hash"] = hash_arguments(arguments)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jti (jti, tool_name, issued_at, expires_at) VALUES (%s, %s, %s, %s)",
            (token_id, tool_name, now, expires),
        )
    return MintedToken(jwt.encode(claims, private_key, algorithm="EdDSA"), token_id)

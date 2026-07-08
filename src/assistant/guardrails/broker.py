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
) -> MintedToken:
    """Mint a single-use JWT authorizing exactly one call to `tool_name` with
    exactly these arguments. Registers the jti; the wrapper consumes it on use."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    token_id = str(uuid.uuid4())
    claims = {
        "jti": token_id,
        "sub": user_id,
        "tool_name": tool_name,
        "arguments_hash": hash_arguments(arguments),
        "iat": now,
        "exp": expires,
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jti (jti, tool_name, issued_at, expires_at) VALUES (%s, %s, %s, %s)",
            (token_id, tool_name, now, expires),
        )
    return MintedToken(jwt.encode(claims, private_key, algorithm="EdDSA"), token_id)

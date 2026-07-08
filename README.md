# ai-assistant-demo

A personal, Jarvis-style AI assistant — voice and an interactive UI as equal peers, built security-first: the permission gate, token broker, and audit log exist before anything they could fail to protect.

## Status: Phase 1 of 6 (security spine) — done

A text conversation can trigger one fake destructive tool (`send_email`), and that tool provably won't execute without a confirmed, single-use, unexpired token.

| Phase | Builds | Status |
|---|---|---|
| 1 — Security spine | Orchestrator loop, model adapter, JWT-verified fake tool, CLI confirmation, audit log | ✅ |
| 2 — Real tools via MCP | MCP client, tool fingerprint registry (TOFU + drift detection) | — |
| 3 — Memory | pgvector fact store, persona loader | — |
| 4 — UI | Web chat, confirmation buttons, memory browsers | — |
| 5 — Voice | Wake word → STT → orchestrator → TTS | — |
| 6 — Deploy | Split to target topology; dev is single-machine by design | — |

## How the gate works

Two import-isolated packages form a real trust boundary:

- **`src/assistant`** — the orchestrator side: hand-controlled tool-calling loop (the single enforcement point), model adapter, CLI confirmation, and a broker that mints short-TTL Ed25519-signed JWTs only after the user confirms. Every step is audited with what triggered it (`user_request` vs `tool_output` — tool output is untrusted and re-enters the same gate).
- **`src/toolwrapper`** — a separate HTTP process in front of the tool, holding only the public key. It verifies signature, expiry, tool binding, and argument binding, then consumes the token's `jti` atomically in Postgres. It can verify but structurally cannot mint; the orchestrator can mint but never verifies its own tokens.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync
uv run python scripts/generate_keys.py   # dev Ed25519 keypair → keys/ (gitignored)
docker compose up -d                     # Postgres on host port 5433, schema auto-applied
cp .env.example .env                     # then add your ANTHROPIC_API_KEY
```

## Run

```sh
uv run fake-tool-server   # terminal 1: the verifying wrapper + fake tool
uv run assistant          # terminal 2: chat; try "email my friend saying hi"
```

Destructive tool calls prompt `Allow? [y/N]` before a token is minted.

## Tests

```sh
uv run pytest
```

[tests/test_bypass.py](tests/test_bypass.py) encodes Phase 1's definition of done: six bypass attempts against a live wrapper — no token, forged signature, expired token, replayed `jti`, tampered arguments, wrong-tool token — all rejected with nothing executed, plus the happy path. Skips if Postgres isn't running.

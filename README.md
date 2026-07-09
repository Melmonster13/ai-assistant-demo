# ai-assistant-demo

A personal, Jarvis-style AI assistant — voice and an interactive UI as equal peers, built security-first: the permission gate, token broker, and audit log exist before anything they could fail to protect.

## Status: Phase 2 of 6 (real tools via MCP) — done

Real MCP servers behind the verifying wrapper: a read-only `notes` server on the low-risk tier and a destructive `files` server on the high tier. Destructive calls provably won't execute without a confirmed, single-use, argument-bound, unexpired token; tool definitions are fingerprinted on first use and re-verified for drift on every connection.

| Phase | Builds | Status |
|---|---|---|
| 1 — Security spine | Orchestrator loop, model adapter, JWT-verified fake tool, CLI confirmation, audit log | ✅ |
| 2 — Real tools via MCP | MCP client, tool fingerprint registry (TOFU + drift detection), tiered tokens | ✅ |
| 3 — Memory | pgvector fact store, persona loader | — |
| 4 — UI | Web chat, confirmation buttons, memory browsers | — |
| 5 — Voice | Wake word → STT → orchestrator → TTS | — |
| 6 — Deploy | Split to target topology; dev is single-machine by design | — |

## How the gate works

Three import-isolated packages form a real trust boundary:

- **`src/assistant`** — the orchestrator side: hand-controlled tool-calling loop (the single enforcement point), model adapter, CLI confirmation, fingerprint registry, and a broker that mints Ed25519-signed JWT permission slips tiered by risk: destructive calls get a single-use, argument-bound, seconds-lived token minted only after the user confirms; read-only calls ride a longer-lived tool-scoped token. Every step is audited with what triggered it (`user_request` vs `tool_output` — tool output is untrusted and re-enters the same gate).
- **`src/toolwrapper`** — a verifying HTTP proxy that spawns and owns one stdio MCP server as a child process, holding only the public key. There is no path to the tool that skips it: discovery and every call go through the wrapper, which verifies signature, expiry, tier, and tool binding — plus argument binding and atomic single-use `jti` consumption at the high tier. Each boundary enforces its own configured tier, so a low-tier token can never authorize a destructive call regardless of what the orchestrator minted. It can verify but structurally cannot mint; the orchestrator can mint but never verifies its own tokens.
- **`src/mcpservers`** — the actual stdio MCP servers (`notes`: read-only; `files`: destructive, sandboxed to a root dir), spawned with an explicit env allowlist rather than inheriting the wrapper's environment.

Tool definitions are trusted on first use: each tool's name + description + input schema is fingerprinted and requires one-time approval. On every reconnect the fingerprint is re-checked; a changed definition (the MCP "rug pull") is logged old-vs-new, requires explicit re-approval, and stays confirmation-forced for the session even if re-approved.

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
uv run tool-wrapper notes   # terminal 1: read-only notes server (low tier)
uv run tool-wrapper files   # terminal 2: destructive files server (high tier)
uv run assistant            # terminal 3: chat; try "read my notes, then save a summary file"
```

First run prompts one-time approval per discovered tool (TOFU). Destructive tool calls prompt `Allow? [y/N]` before a token is minted; reads don't.

## Tests

```sh
uv run pytest
```

[tests/test_bypass.py](tests/test_bypass.py) proves the destructive tool won't run without a confirmed, single-use, unexpired token — seven bypass attempts against a live wrapper fronting the real MCP server (no token, forged signature, expired, replayed `jti`, tampered arguments, wrong-tool token, low-tier token at the high boundary), each asserting nothing reached the filesystem. [tests/test_registry.py](tests/test_registry.py) covers TOFU and rug-pull drift; [tests/test_tiering.py](tests/test_tiering.py) covers both tiers' token rules; [tests/test_orchestrator.py](tests/test_orchestrator.py) runs the loop end-to-end. Skips if Postgres isn't running.

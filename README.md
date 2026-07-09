# ai-assistant-demo

A personal, Jarvis-style AI assistant — voice and an interactive UI as equal peers, built security-first: the permission gate, token broker, and audit log exist before anything they could fail to protect.

## Status: Phase 3 of 6 (memory) — done

Two-tier memory: a pgvector fact store keyed by `user_id` with Postgres row-level security actually enforced (memory connects as a non-superuser role, so a query can only ever touch the current user's rows), and a persona/profile loaded from a markdown folder into the system prompt. Relevant facts are auto-recalled into context each turn; the model saves new ones with a `remember_fact` tool. Memory is a direct integration, not an MCP tool.

| Phase | Builds | Status |
|---|---|---|
| 1 — Security spine | Orchestrator loop, model adapter, JWT-verified fake tool, CLI confirmation, audit log | ✅ |
| 2 — Real tools via MCP | MCP client, tool fingerprint registry (TOFU + drift detection), tiered tokens | ✅ |
| 3 — Memory | pgvector fact store (RLS), persona loader, embedding adapter | ✅ |
| 4 — UI | Web chat, confirmation buttons, memory browsers | — |
| 5 — Voice | Wake word → STT → orchestrator → TTS | — |
| 6 — Deploy | Split to target topology; dev is single-machine by design | — |

## How the gate works

Three import-isolated packages form a real trust boundary:

- **`src/assistant`** — the orchestrator side: hand-controlled tool-calling loop (the single enforcement point), model adapter, CLI confirmation, fingerprint registry, and a broker that mints Ed25519-signed JWT permission slips tiered by risk: destructive calls get a single-use, argument-bound, seconds-lived token minted only after the user confirms; read-only calls ride a longer-lived tool-scoped token. Every step is audited with what triggered it (`user_request` vs `tool_output` — tool output is untrusted and re-enters the same gate).
- **`src/toolwrapper`** — a verifying HTTP proxy that spawns and owns one stdio MCP server as a child process, holding only the public key. There is no path to the tool that skips it: discovery and every call go through the wrapper, which verifies signature, expiry, tier, and tool binding — plus argument binding and atomic single-use `jti` consumption at the high tier. Each boundary enforces its own configured tier, so a low-tier token can never authorize a destructive call regardless of what the orchestrator minted. It can verify but structurally cannot mint; the orchestrator can mint but never verifies its own tokens.
- **`src/mcpservers`** — the actual stdio MCP servers (`notes`: read-only; `files`: destructive, sandboxed to a root dir), spawned with an explicit env allowlist rather than inheriting the wrapper's environment.

Tool definitions are trusted on first use: each tool's name + description + input schema is fingerprinted and requires one-time approval. On every reconnect the fingerprint is re-checked; a changed definition (the MCP "rug pull") is logged old-vs-new, requires explicit re-approval, and stays confirmation-forced for the session even if re-approved.

## Memory

Memory is a direct integration (`src/assistant/memory/`), not an MCP tool — it's internal and has no separate trust boundary. Two tiers:

- **Fact-recall** — a Postgres + pgvector store keyed by `user_id`. Row-level security is enforced, not just declared: memory connects as a dedicated non-superuser role and each operation binds `app.current_user_id`, so the RLS policy limits every query to that user's rows in the database itself, even if the SQL omits a `WHERE`. Relevant facts are auto-recalled by semantic similarity each turn and injected into the system prompt; the model persists new facts with a `remember_fact` tool (an internal low-risk write — audited, but no confirmation or token).
- **Persona/profile** — hand-authored markdown in a folder (the dev stand-in for a synced vault), concatenated into the system prompt to shape tone.

Embeddings are an adapter chosen by config: `local` uses a small local sentence-transformers model (private, no API key; `uv sync --extra local-embeddings`), `hashing` is a zero-dependency fallback. Both produce 384-dim vectors, so the schema is backend-independent.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync                                  # add --extra local-embeddings for the local embedding model
uv run python scripts/generate_keys.py   # dev Ed25519 keypair → keys/ (gitignored)
docker compose up -d                     # Postgres on host port 5433, schema auto-applied
cp .env.example .env                     # then add your ANTHROPIC_API_KEY
```

The default `EMBEDDING_BACKEND=local` needs the `local-embeddings` extra; set it to `hashing` to run without extra dependencies.

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

[tests/test_bypass.py](tests/test_bypass.py) proves the destructive tool won't run without a confirmed, single-use, unexpired token — seven bypass attempts against a live wrapper fronting the real MCP server (no token, forged signature, expired, replayed `jti`, tampered arguments, wrong-tool token, low-tier token at the high boundary), each asserting nothing reached the filesystem. [tests/test_registry.py](tests/test_registry.py) covers TOFU and rug-pull drift; [tests/test_tiering.py](tests/test_tiering.py) covers both tiers' token rules; [tests/test_orchestrator.py](tests/test_orchestrator.py) runs the loop end-to-end. [tests/test_memory.py](tests/test_memory.py) covers semantic recall and RLS user isolation (including that a raw un-filtered `SELECT` still can't cross users); [tests/test_memory_orchestrator.py](tests/test_memory_orchestrator.py) covers persona injection, auto-recall, and the `remember_fact` path. Skips if Postgres isn't running.

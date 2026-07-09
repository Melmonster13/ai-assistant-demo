-- Phase 3: fact-recall memory (pgvector), user_id-keyed with RLS enforced from
-- day one. Distinct from persona/profile (hand-authored markdown, no DB role).
--
-- RLS only bites for a non-superuser role, so the orchestrator's memory access
-- uses a dedicated login role (assistant_app) rather than the superuser the rest
-- of the app connects as. This is the "read/write tied to user_id" control made
-- structural, and a dev-scale stand-in for the per-service OS users Phase 6 adds.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE facts (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    TEXT NOT NULL,
    content    TEXT NOT NULL,
    embedding  vector(384) NOT NULL,  -- 384 = all-MiniLM-L6-v2 / hashing embedder
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX facts_embedding_idx ON facts USING hnsw (embedding vector_cosine_ops);

-- Row isolation: a row is visible/writable only when app.current_user_id (a
-- per-request session setting) matches its user_id. Unset -> NULL -> no rows
-- (deny by default). FORCE so it applies even to the table owner.
ALTER TABLE facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE facts FORCE ROW LEVEL SECURITY;
CREATE POLICY facts_user_isolation ON facts
    USING (user_id = current_setting('app.current_user_id', true))
    WITH CHECK (user_id = current_setting('app.current_user_id', true));

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'assistant_app') THEN
        CREATE ROLE assistant_app LOGIN PASSWORD 'assistant_app';
    END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON facts TO assistant_app;

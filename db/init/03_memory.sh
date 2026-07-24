#!/bin/bash
# Phase 3: fact-recall memory (pgvector), user_id-keyed with RLS enforced from
# day one. Distinct from persona/profile (hand-authored markdown, no DB role).
#
# RLS only bites for a non-superuser role, so the orchestrator's memory access
# uses a dedicated login role (assistant_app) rather than the superuser the rest
# of the app connects as. This is the "read/write tied to user_id" control made
# structural, and a dev-scale stand-in for the per-service OS users Phase 6 adds.
#
# Shell (not plain .sql) because the assistant_app password must come from the
# environment, and docker-entrypoint-initdb.d does no env expansion on .sql
# files. Fail loudly if it is unset — never fall back to a guessable default.
set -euo pipefail

if [ -z "${ASSISTANT_APP_PASSWORD:-}" ]; then
    echo "ERROR: ASSISTANT_APP_PASSWORD is unset — refusing to create the assistant_app role without a password." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v app_password="$ASSISTANT_APP_PASSWORD" <<-'EOSQL'
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

	-- Create-or-update the role with the password from the environment. The psql
	-- variable is interpolated here (a regular statement, not a dollar-quoted
	-- body) and format(%L) quotes it safely; \gexec then runs the built command.
	SELECT format(
	    '%s ROLE assistant_app LOGIN PASSWORD %L',
	    CASE WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = 'assistant_app')
	         THEN 'ALTER' ELSE 'CREATE' END,
	    :'app_password'
	)
	\gexec

	GRANT SELECT, INSERT, UPDATE, DELETE ON facts TO assistant_app;
	EOSQL

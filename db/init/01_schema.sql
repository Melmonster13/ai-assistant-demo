-- Phase 1 schema: audit log + single-use token (jti) tracking.
-- Security/system state, kept separate from fact-recall memory (Phase 3).

CREATE TABLE audit_log (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id       TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    -- requested | confirmed | denied | token_minted | executed | rejected_by_wrapper
    event         TEXT NOT NULL,
    -- what triggered the call: user_request | tool_output
    triggered_by  TEXT NOT NULL,
    detail        TEXT,
    jti           TEXT
);

CREATE TABLE jti (
    jti        TEXT PRIMARY KEY,
    tool_name  TEXT NOT NULL,
    issued_at  TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ
);

-- Phase 2: MCP tool definition integrity (TOFU fingerprint baseline + drift).
-- Security/system state, deliberately separate from fact-recall memory.

CREATE TABLE tool_registry (
    server_id       TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    -- sha256 over canonical JSON of {name, description, input_schema} —
    -- the parts exploitable for description-injection / bait-and-switch
    fingerprint     TEXT NOT NULL,
    definition      JSONB NOT NULL,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_verified   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- approved | denied
    approval_status TEXT NOT NULL,
    PRIMARY KEY (server_id, tool_name)
);

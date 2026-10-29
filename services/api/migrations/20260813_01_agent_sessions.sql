CREATE TABLE openair_agent_sessions (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_version TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL,
    agent_display_name TEXT NOT NULL,
    active_step_key TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_openair_agent_sessions_scope_updated
    ON openair_agent_sessions(owner_id, workspace_id, updated_at DESC);

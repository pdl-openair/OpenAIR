CREATE TABLE research_start_sessions (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'running',
    reserved_task_id TEXT NOT NULL,
    reserved_artifact_id TEXT NOT NULL,
    result_payload JSONB NULL,
    user_message TEXT NULL,
    failure_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(session_id, owner_id, workspace_id),
    CHECK (session_id ~ '^intent_[0-9a-f]{12}$'),
    CHECK (reserved_task_id ~ '^task_[0-9a-f]{32}$'),
    CHECK (reserved_artifact_id ~ '^artifact_[0-9a-f]{32}$'),
    CHECK (state IN ('running', 'completed', 'failed', 'external_result_unknown')),
    CHECK (state NOT IN ('failed', 'external_result_unknown') OR result_payload IS NULL),
    CHECK (
        (state = 'running' AND result_payload IS NULL AND user_message IS NULL AND failure_code IS NULL)
        OR (state = 'completed' AND result_payload IS NOT NULL AND length(user_message) > 0 AND failure_code IS NULL)
        OR (state = 'failed' AND result_payload IS NULL AND length(user_message) > 0 AND length(failure_code) > 0)
        OR (state = 'external_result_unknown' AND result_payload IS NULL
            AND length(user_message) > 0 AND failure_code = 'external_result_unknown')
    )
);

CREATE INDEX idx_research_start_sessions_scope
    ON research_start_sessions(owner_id, workspace_id, created_at DESC);
CREATE UNIQUE INDEX uq_research_start_sessions_reserved_task
    ON research_start_sessions(reserved_task_id);
CREATE UNIQUE INDEX uq_research_start_sessions_reserved_artifact
    ON research_start_sessions(reserved_artifact_id);

CREATE TABLE research_start_requests (
    request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    lease_owner TEXT NULL,
    lease_epoch BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ NULL,
    request_payload JSONB NOT NULL,
    result_payload JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(owner_id, workspace_id, client_request_id),
    FOREIGN KEY (session_id, owner_id, workspace_id)
        REFERENCES research_start_sessions(session_id, owner_id, workspace_id) ON DELETE RESTRICT,
    CHECK (request_id ~ '^rsr_[0-9a-f]{32}$'),
    CHECK (state IN ('queued', 'leased', 'succeeded', 'failed', 'unknown')),
    CHECK (length(fingerprint) = 64),
    CHECK (jsonb_typeof(request_payload) = 'object'),
    CHECK ((state = 'leased') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK (state = 'succeeded' OR result_payload IS NULL),
    CHECK (state <> 'succeeded' OR result_payload IS NOT NULL)
);

CREATE INDEX idx_research_start_requests_claim
    ON research_start_requests(state, created_at, request_id) WHERE state = 'queued';
CREATE INDEX idx_research_start_requests_expiry
    ON research_start_requests(lease_expires_at, request_id) WHERE state = 'leased';
CREATE INDEX idx_research_start_requests_session
    ON research_start_requests(session_id, owner_id, workspace_id, created_at, request_id);

CREATE TABLE research_start_attempts (
    attempt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES research_start_requests(request_id) ON DELETE RESTRICT,
    phase TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'started',
    fence_epoch BIGINT NOT NULL,
    result_payload JSONB NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ NULL,
    CHECK (attempt_id ~ '^rsa_[0-9a-f]{32}$'),
    CHECK (phase = 'research_planner'),
    CHECK (ordinal > 0),
    CHECK (fence_epoch > 0),
    CHECK (state IN ('started', 'succeeded', 'failed', 'unknown')),
    CHECK ((state = 'started' AND finished_at IS NULL AND result_payload IS NULL)
        OR (state <> 'started' AND finished_at IS NOT NULL)),
    UNIQUE(request_id, phase, ordinal)
);

CREATE UNIQUE INDEX uq_research_start_attempts_one_started
    ON research_start_attempts(request_id) WHERE state = 'started';
CREATE UNIQUE INDEX uq_research_start_attempts_one_succeeded_phase
    ON research_start_attempts(request_id, phase) WHERE state = 'succeeded';
CREATE INDEX idx_research_start_attempts_request
    ON research_start_attempts(request_id, state, phase, ordinal);

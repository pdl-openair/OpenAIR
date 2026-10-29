-- Durable queue for every user-triggered operation that may call a model.
-- HTTP persists intent and returns; only the durable worker executes the model.
CREATE TABLE openair_model_jobs (
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    job_kind TEXT NOT NULL,
    expected_revision BIGINT NULL,
    command_hash TEXT NOT NULL,
    command_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    state TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    lease_owner TEXT NULL,
    lease_epoch BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    runtime_source_version TEXT NOT NULL,
    result_payload JSONB NULL,
    last_error_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (resource_kind, resource_id, owner_id, workspace_id, job_id),
    CHECK (resource_kind IN ('task', 'agent_session')),
    CHECK (length(btrim(resource_id)) > 0),
    CHECK (length(btrim(owner_id)) > 0),
    CHECK (length(btrim(workspace_id)) > 0),
    CHECK (length(btrim(job_id)) > 0),
    CHECK (job_kind IN (
        'execution_selection',
        'research_design_agent_revision',
        'research_design_plan_revision',
        'agent_session_turn'
    )),
    CHECK (expected_revision IS NULL OR expected_revision >= 0),
    CHECK (command_hash ~ '^sha256:[0-9a-f]{64}$'),
    CHECK (state IN ('queued', 'leased', 'succeeded', 'blocked', 'failed')),
    CHECK (attempt_count >= 0 AND max_attempts BETWEEN 1 AND 3),
    CHECK (lease_epoch >= 0),
    CHECK ((state = 'leased') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK (state IN ('succeeded', 'blocked') OR result_payload IS NULL),
    CHECK (state NOT IN ('succeeded', 'blocked') OR result_payload IS NOT NULL)
);

CREATE INDEX idx_openair_model_jobs_claim
    ON openair_model_jobs(state, next_attempt_at, created_at)
    WHERE state IN ('queued', 'leased');

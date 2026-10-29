ALTER TABLE openair_tasks ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE openair_artifacts ADD COLUMN IF NOT EXISTS source_execution_run_id TEXT NULL;

CREATE TABLE IF NOT EXISTS openair_execution_runs (
    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, task_revision BIGINT NOT NULL,
    run_number INTEGER NOT NULL, run_kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL, input_snapshot JSONB NOT NULL, target_substage TEXT NULL,
    source_mode TEXT NOT NULL, capability_call_id TEXT NOT NULL, state TEXT NOT NULL,
    lease_owner TEXT NULL, lease_epoch BIGINT NOT NULL DEFAULT 0, lease_expires_at TIMESTAMPTZ NULL,
    external_action_started BOOLEAN NOT NULL DEFAULT FALSE, failure_classification TEXT NULL,
    next_event_sequence BIGINT NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NULL, ended_at TIMESTAMPTZ NULL,
    UNIQUE(task_id, run_number), UNIQUE(owner_id, workspace_id, task_id, run_kind, idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_initial_execution_per_task ON openair_execution_runs(task_id, run_kind) WHERE run_kind = 'initial_start';
CREATE INDEX IF NOT EXISTS idx_openair_execution_claim ON openair_execution_runs(state, lease_expires_at, created_at);
CREATE TABLE IF NOT EXISTS openair_execution_run_events (
    run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL, type TEXT NOT NULL, payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(run_id, sequence)
);
CREATE TABLE IF NOT EXISTS openair_execution_callback_rejections (
    rejection_id TEXT PRIMARY KEY, run_id TEXT NULL, received_call_id TEXT NULL, reason TEXT NOT NULL,
    lease_epoch BIGINT NULL, worker_id TEXT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_openair_callback_rejections_run ON openair_execution_callback_rejections(run_id, occurred_at DESC);
CREATE TABLE IF NOT EXISTS openair_artifact_uploads (
    upload_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    capability_call_id TEXT NULL, lease_owner TEXT NOT NULL, lease_epoch BIGINT NOT NULL,
    object_key TEXT NOT NULL, expected_size BIGINT NULL, expected_sha256 TEXT NULL, state TEXT NOT NULL,
    cleanup_state TEXT NOT NULL DEFAULT 'not_required', artifact_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), confirmed_at TIMESTAMPTZ NULL, UNIQUE(object_key)
);
CREATE TABLE IF NOT EXISTS openair_execution_idempotency (
    owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    operation TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(owner_id, workspace_id, task_id, operation, idempotency_key)
);
CREATE TABLE IF NOT EXISTS openair_execution_capability_calls (
    call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE, state TEXT NOT NULL,
    source_mode TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), started_at TIMESTAMPTZ NULL, ended_at TIMESTAMPTZ NULL
);
CREATE TABLE IF NOT EXISTS openair_execution_artifact_associations (
    upload_id TEXT PRIMARY KEY REFERENCES openair_artifact_uploads(upload_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    call_id TEXT NOT NULL REFERENCES openair_execution_capability_calls(call_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

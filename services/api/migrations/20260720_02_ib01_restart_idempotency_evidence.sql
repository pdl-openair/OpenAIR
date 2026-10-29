-- IB-01A.2 durable evidence.  These facts are API-owned and append-only;
-- workers only cause them through the authenticated HTTP API.
ALTER TABLE openair_artifacts ADD COLUMN IF NOT EXISTS source_mode TEXT NULL;

CREATE TABLE IF NOT EXISTS openair_execution_start_attempts (
    attempt_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT 'initial_start',
    idempotency_key_sha256 TEXT NOT NULL,
    outcome TEXT NOT NULL,
    canonical_run_id TEXT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_openair_execution_start_attempts_task
    ON openair_execution_start_attempts(task_id, occurred_at);

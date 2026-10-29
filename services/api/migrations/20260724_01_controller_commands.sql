CREATE TABLE IF NOT EXISTS openair_controller_commands (
    command_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('supplement_input')),
    payload JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('received', 'applied', 'rejected', 'failed')),
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    applied_run_id TEXT NULL REFERENCES openair_execution_runs(run_id) ON DELETE SET NULL,
    applied_work_unit_id TEXT NULL REFERENCES openair_execution_work_units(work_unit_id) ON DELETE SET NULL,
    user_message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner_id, workspace_id, task_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_openair_controller_commands_dispatch
    ON openair_controller_commands(task_id, owner_id, workspace_id, state, created_at);

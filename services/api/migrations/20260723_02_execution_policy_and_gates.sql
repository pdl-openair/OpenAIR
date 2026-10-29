-- PostgreSQL is the sole authority for execution policy and human gates.
-- task.payload.executionMode is retained only as a legacy compatibility
-- projection for clients that have not moved to the policy endpoint.

CREATE TABLE IF NOT EXISTS openair_task_execution_policies (
    task_id TEXT PRIMARY KEY REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    current_mode TEXT NOT NULL CHECK (current_mode IN ('automatic_research', 'stage_confirmation', 'manual_step')),
    pending_mode TEXT NULL CHECK (pending_mode IN ('automatic_research', 'stage_confirmation', 'manual_step')),
    policy_version BIGINT NOT NULL CHECK (policy_version > 0),
    pending_policy_version BIGINT NULL CHECK (pending_policy_version > policy_version),
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((pending_mode IS NULL) = (pending_policy_version IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_openair_task_execution_policies_scope
    ON openair_task_execution_policies(owner_id, workspace_id);

-- Backfill every pre-policy task without treating its payload as ongoing
-- authority. New writes update this table first and only project outward.
INSERT INTO openair_task_execution_policies
    (task_id, owner_id, workspace_id, current_mode, policy_version, changed_by)
SELECT task_id, owner_id, workspace_id,
       CASE payload->>'executionMode'
         WHEN 'automatic_research' THEN 'automatic_research'
         WHEN 'manual_step' THEN 'manual_step'
         ELSE 'stage_confirmation'
       END,
       1,
       COALESCE(NULLIF(payload->>'createdBy', ''), owner_id)
FROM openair_tasks
ON CONFLICT (task_id) DO NOTHING;

ALTER TABLE openair_execution_work_units
    ADD COLUMN IF NOT EXISTS dispatch_mode TEXT NOT NULL DEFAULT 'stage_confirmation'
        CHECK (dispatch_mode IN ('automatic_research', 'stage_confirmation', 'manual_step')),
    ADD COLUMN IF NOT EXISTS policy_version BIGINT NOT NULL DEFAULT 1 CHECK (policy_version > 0),
    ADD COLUMN IF NOT EXISTS boundary_kind TEXT NOT NULL DEFAULT 'dispatch';

CREATE TABLE IF NOT EXISTS openair_execution_gates (
    gate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    gate_kind TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'resolved', 'cancelled')),
    work_unit_id TEXT NULL REFERENCES openair_execution_work_units(work_unit_id) ON DELETE SET NULL,
    artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    reason TEXT NOT NULL,
    policy_version BIGINT NOT NULL CHECK (policy_version > 0),
    resolved_by TEXT NULL,
    resolved_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (jsonb_typeof(artifact_refs) = 'array'),
    CHECK ((state = 'pending' AND resolved_by IS NULL AND resolved_at IS NULL)
        OR (state IN ('resolved', 'cancelled') AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)),
    UNIQUE(run_id, gate_kind, work_unit_id)
);
CREATE INDEX IF NOT EXISTS idx_openair_execution_gates_run_state
    ON openair_execution_gates(run_id, state, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_execution_gate_identity
    ON openair_execution_gates(run_id, gate_kind, COALESCE(work_unit_id, ''));

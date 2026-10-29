-- A durable run may contain multiple independently attributable units of work.
-- Keep the legacy run.capability_call_id as the compatibility pointer for the
-- original initial call; capability calls themselves are now one-to-many.
ALTER TABLE openair_execution_capability_calls
    DROP CONSTRAINT IF EXISTS openair_execution_capability_calls_run_id_key;

-- The base durable-execution migration owns the run lease columns.  This
-- migration only extends calls and never creates a parallel lease authority.

CREATE TABLE IF NOT EXISTS openair_execution_work_units (
    work_unit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    unit_key TEXT NOT NULL,
    parent_work_unit_id TEXT NULL REFERENCES openair_execution_work_units(work_unit_id) ON DELETE SET NULL,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    stage_key TEXT NULL,
    substage_key TEXT NULL,
    model_profile TEXT NULL,
    objective TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'blocked', 'unknown')),
    visibility TEXT NOT NULL DEFAULT 'main' CHECK (visibility IN ('main', 'detail')),
    title TEXT NOT NULL,
    summary TEXT NULL,
    decision_summary TEXT NULL,
    input_memory_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NULL,
    ended_at TIMESTAMPTZ NULL,
    CHECK (sequence > 0),
    CHECK (jsonb_typeof(input_memory_refs) = 'array'),
    CHECK (jsonb_typeof(input_artifact_refs) = 'array'),
    CHECK (jsonb_typeof(output_artifact_refs) = 'array'),
    UNIQUE(run_id, sequence),
    UNIQUE(run_id, unit_key)
);
CREATE INDEX IF NOT EXISTS idx_openair_work_units_task_scope
    ON openair_execution_work_units(task_id, owner_id, workspace_id, sequence);

ALTER TABLE openair_execution_capability_calls
    ADD COLUMN IF NOT EXISTS work_unit_id TEXT NULL REFERENCES openair_execution_work_units(work_unit_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS requesting_agent_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS skill_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS invocation_kind TEXT NOT NULL DEFAULT 'skill',
    ADD COLUMN IF NOT EXISTS external_action_started BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS failure_classification TEXT NULL,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL,
    ADD COLUMN IF NOT EXISTS request_fingerprint TEXT NULL,
    ADD COLUMN IF NOT EXISTS fence_epoch BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS attempt_number INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS result_summary TEXT NULL,
    ADD COLUMN IF NOT EXISTS input_memory_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS input_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS output_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS idx_openair_capability_calls_work_unit
    ON openair_execution_capability_calls(work_unit_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_capability_call_invocation_idempotency
    ON openair_execution_capability_calls(work_unit_id, requesting_agent_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS openair_execution_handoffs (
    handoff_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    from_work_unit_id TEXT NOT NULL REFERENCES openair_execution_work_units(work_unit_id) ON DELETE CASCADE,
    to_work_unit_id TEXT NOT NULL REFERENCES openair_execution_work_units(work_unit_id) ON DELETE CASCADE,
    from_agent_id TEXT NOT NULL,
    to_agent_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    input_memory_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (from_work_unit_id <> to_work_unit_id),
    CHECK (jsonb_typeof(input_memory_refs) = 'array'),
    CHECK (jsonb_typeof(input_artifact_refs) = 'array'),
    CHECK (jsonb_typeof(output_artifact_refs) = 'array'),
    UNIQUE(from_work_unit_id, to_work_unit_id)
);
CREATE INDEX IF NOT EXISTS idx_openair_handoffs_task_scope
    ON openair_execution_handoffs(task_id, owner_id, workspace_id, created_at);

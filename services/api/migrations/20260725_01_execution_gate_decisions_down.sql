ALTER TABLE openair_execution_gates
    DROP CONSTRAINT IF EXISTS ck_openair_execution_gate_resolution_shape,
    DROP CONSTRAINT IF EXISTS ck_openair_execution_gate_resolution_action,
    DROP COLUMN IF EXISTS successor_run_id,
    DROP COLUMN IF EXISTS resolution_note,
    DROP COLUMN IF EXISTS resolution_action;

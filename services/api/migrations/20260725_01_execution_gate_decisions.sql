ALTER TABLE openair_execution_gates
    ADD COLUMN IF NOT EXISTS resolution_action TEXT NULL,
    ADD COLUMN IF NOT EXISTS resolution_note TEXT NULL,
    ADD COLUMN IF NOT EXISTS successor_run_id TEXT NULL REFERENCES openair_execution_runs(run_id);

UPDATE openair_execution_gates
SET resolution_action = 'legacy_resolved'
WHERE state IN ('resolved', 'cancelled') AND resolution_action IS NULL;

ALTER TABLE openair_execution_gates
    DROP CONSTRAINT IF EXISTS ck_openair_execution_gate_resolution_action,
    DROP CONSTRAINT IF EXISTS ck_openair_execution_gate_resolution_shape,
    ADD CONSTRAINT ck_openair_execution_gate_resolution_action CHECK (
        resolution_action IS NULL OR resolution_action IN (
            'terminate_run', 'start_revision_run', 'legacy_resolved'
        )
    ),
    ADD CONSTRAINT ck_openair_execution_gate_resolution_shape CHECK (
        (state = 'pending' AND resolution_action IS NULL AND resolution_note IS NULL AND successor_run_id IS NULL)
        OR (state = 'cancelled' AND resolution_action IN ('terminate_run', 'legacy_resolved') AND successor_run_id IS NULL)
        OR (state = 'resolved' AND (
            (resolution_action = 'start_revision_run' AND successor_run_id IS NOT NULL)
            OR (resolution_action = 'legacy_resolved' AND successor_run_id IS NULL)
        ))
    );

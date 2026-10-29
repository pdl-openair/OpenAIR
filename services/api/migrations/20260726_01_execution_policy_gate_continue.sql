ALTER TABLE openair_execution_gates
    DROP CONSTRAINT IF EXISTS ck_openair_execution_gate_resolution_shape,
    DROP CONSTRAINT IF EXISTS ck_openair_execution_gate_resolution_action,
    ADD CONSTRAINT ck_openair_execution_gate_resolution_action CHECK (
        resolution_action IS NULL OR resolution_action IN (
            'continue_run', 'terminate_run', 'start_revision_run', 'legacy_resolved'
        )
    ),
    ADD CONSTRAINT ck_openair_execution_gate_resolution_shape CHECK (
        (state = 'pending' AND resolution_action IS NULL AND resolution_note IS NULL AND successor_run_id IS NULL)
        OR (state = 'cancelled' AND resolution_action IN ('terminate_run', 'legacy_resolved') AND successor_run_id IS NULL)
        OR (state = 'resolved' AND (
            (gate_kind = 'artifact_review' AND resolution_action = 'start_revision_run' AND successor_run_id IS NOT NULL)
            OR (gate_kind = 'execution_policy' AND resolution_action = 'continue_run' AND successor_run_id IS NULL)
            OR (resolution_action = 'legacy_resolved' AND successor_run_id IS NULL)
        ))
    );

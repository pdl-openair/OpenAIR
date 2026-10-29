ALTER TABLE openair_controller_commands
    ADD COLUMN IF NOT EXISTS submitted_task_revision BIGINT,
    ADD COLUMN IF NOT EXISTS lifecycle_kind TEXT,
    ADD COLUMN IF NOT EXISTS target_boundary JSONB,
    ADD COLUMN IF NOT EXISTS target_execution_decision_id TEXT,
    ADD COLUMN IF NOT EXISTS applied_execution_decision_id TEXT,
    ADD COLUMN IF NOT EXISTS applied_canonical_work_unit_id TEXT,
    ADD COLUMN IF NOT EXISTS terminal_reason_code TEXT;

UPDATE openair_controller_commands AS command
SET submitted_task_revision = COALESCE(command.submitted_task_revision, task.revision),
    lifecycle_kind = COALESCE(
        command.lifecycle_kind,
        CASE
            WHEN task.payload ? 'researchDesignVersions'
             AND task.payload ? 'executionDecisions'
             AND task.payload ? 'workUnits'
            THEN 'canonical_v1_2'
            ELSE 'legacy_durable_run'
        END
    ),
    target_boundary = COALESCE(
        command.target_boundary,
        jsonb_build_object(
            'knownWorkUnitIds',
            COALESCE(
                (
                    SELECT jsonb_agg(item ->> 'workUnitId' ORDER BY item ->> 'workUnitId')
                    FROM jsonb_array_elements(COALESCE(task.payload -> 'workUnits', '[]'::jsonb)) AS item
                    WHERE COALESCE(item ->> 'workUnitId', '') <> ''
                ),
                '[]'::jsonb
            ),
            'submittedAfterExecutionDecisionId',
            NULL
        )
    ),
    applied_execution_decision_id = COALESCE(
        command.applied_execution_decision_id,
        (
            SELECT item ->> 'executionDecisionId'
            FROM jsonb_array_elements(COALESCE(task.payload -> 'workUnits', '[]'::jsonb)) AS item
            WHERE item ->> 'workUnitId' = command.applied_work_unit_id
            LIMIT 1
        )
    ),
    user_message = CASE
        WHEN task.payload ? 'researchDesignVersions'
         AND task.payload ? 'executionDecisions'
         AND task.payload ? 'workUnits'
        THEN ''
        ELSE command.user_message
    END,
    state = CASE
        WHEN task.payload ? 'researchDesignVersions'
         AND task.payload ? 'executionDecisions'
         AND task.payload ? 'workUnits'
         AND (command.applied_run_id IS NOT NULL OR command.applied_work_unit_id IS NOT NULL)
        THEN 'rejected'
        ELSE command.state
    END,
    terminal_reason_code = CASE
        WHEN task.payload ? 'researchDesignVersions'
         AND task.payload ? 'executionDecisions'
         AND task.payload ? 'workUnits'
         AND (command.applied_run_id IS NOT NULL OR command.applied_work_unit_id IS NOT NULL)
        THEN 'legacy_application_invalidated'
        ELSE command.terminal_reason_code
    END,
    applied_run_id = CASE
        WHEN task.payload ? 'researchDesignVersions'
         AND task.payload ? 'executionDecisions'
         AND task.payload ? 'workUnits'
        THEN NULL
        ELSE command.applied_run_id
    END,
    applied_work_unit_id = CASE
        WHEN task.payload ? 'researchDesignVersions'
         AND task.payload ? 'executionDecisions'
         AND task.payload ? 'workUnits'
        THEN NULL
        ELSE command.applied_work_unit_id
    END
FROM openair_tasks AS task
WHERE task.task_id = command.task_id;

ALTER TABLE openair_controller_commands
    ALTER COLUMN submitted_task_revision SET NOT NULL,
    ALTER COLUMN lifecycle_kind SET NOT NULL,
    ALTER COLUMN target_boundary SET NOT NULL;

ALTER TABLE openair_controller_commands
    DROP CONSTRAINT IF EXISTS openair_controller_commands_lifecycle_kind_check,
    DROP CONSTRAINT IF EXISTS openair_controller_commands_canonical_legacy_fence_check;
ALTER TABLE openair_controller_commands
    ADD CONSTRAINT openair_controller_commands_lifecycle_kind_check
        CHECK (lifecycle_kind IN ('canonical_v1_2', 'legacy_durable_run')),
    ADD CONSTRAINT openair_controller_commands_canonical_legacy_fence_check
        CHECK (
            lifecycle_kind = 'legacy_durable_run'
            OR (applied_run_id IS NULL AND applied_work_unit_id IS NULL)
        );

CREATE INDEX IF NOT EXISTS idx_openair_controller_commands_canonical_boundary
    ON openair_controller_commands(task_id, owner_id, workspace_id, lifecycle_kind, state, created_at, command_id);

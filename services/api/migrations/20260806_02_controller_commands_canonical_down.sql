UPDATE openair_controller_commands
SET user_message = COALESCE(NULLIF(user_message, ''), payload ->> 'text', '')
WHERE lifecycle_kind = 'canonical_v1_2';

DROP INDEX IF EXISTS idx_openair_controller_commands_canonical_boundary;
ALTER TABLE openair_controller_commands
    DROP CONSTRAINT IF EXISTS openair_controller_commands_canonical_legacy_fence_check,
    DROP CONSTRAINT IF EXISTS openair_controller_commands_lifecycle_kind_check,
    DROP COLUMN IF EXISTS terminal_reason_code,
    DROP COLUMN IF EXISTS applied_canonical_work_unit_id,
    DROP COLUMN IF EXISTS applied_execution_decision_id,
    DROP COLUMN IF EXISTS target_execution_decision_id,
    DROP COLUMN IF EXISTS target_boundary,
    DROP COLUMN IF EXISTS lifecycle_kind,
    DROP COLUMN IF EXISTS submitted_task_revision;

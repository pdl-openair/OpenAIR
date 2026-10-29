DROP INDEX IF EXISTS idx_openair_controller_commands_progression_recovery;
ALTER TABLE openair_controller_commands
    DROP COLUMN IF EXISTS progression_lease_owner,
    DROP COLUMN IF EXISTS progression_lease_expires_at,
    DROP COLUMN IF EXISTS progression_attempt_count,
    DROP COLUMN IF EXISTS progression_last_error,
    DROP COLUMN IF EXISTS progression_next_attempt_at;

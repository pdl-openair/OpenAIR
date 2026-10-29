ALTER TABLE openair_controller_commands
    ADD COLUMN IF NOT EXISTS progression_lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS progression_lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS progression_attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS progression_last_error TEXT,
    ADD COLUMN IF NOT EXISTS progression_next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_openair_controller_commands_progression_recovery
    ON openair_controller_commands(
        lifecycle_kind, state, progression_next_attempt_at,
        progression_lease_expires_at, created_at, command_id
    );

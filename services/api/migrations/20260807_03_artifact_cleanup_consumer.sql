ALTER TABLE openair_artifact_cleanup_outbox
    ADD COLUMN IF NOT EXISTS lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS terminal_at TIMESTAMPTZ;

DROP INDEX IF EXISTS idx_openair_artifact_cleanup_pending;
CREATE INDEX idx_openair_artifact_cleanup_pending
    ON openair_artifact_cleanup_outbox(
        state, next_attempt_at, lease_expires_at, cleanup_id
    );

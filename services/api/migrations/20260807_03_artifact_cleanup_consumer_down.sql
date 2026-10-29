DROP INDEX IF EXISTS idx_openair_artifact_cleanup_pending;
CREATE INDEX idx_openair_artifact_cleanup_pending
    ON openair_artifact_cleanup_outbox(state, updated_at, cleanup_id);
ALTER TABLE openair_artifact_cleanup_outbox
    DROP COLUMN IF EXISTS lease_owner,
    DROP COLUMN IF EXISTS lease_expires_at,
    DROP COLUMN IF EXISTS next_attempt_at,
    DROP COLUMN IF EXISTS terminal_at;

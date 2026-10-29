CREATE TABLE IF NOT EXISTS openair_artifact_cleanup_outbox (
    cleanup_id       TEXT PRIMARY KEY,
    task_id          TEXT NULL,
    artifact_id      TEXT NULL,
    storage_type     TEXT NOT NULL,
    storage_scope    TEXT NOT NULL DEFAULT '',
    storage_ref      TEXT NOT NULL,
    reason_code      TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'pending',
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT NULL,
    lease_owner      TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    terminal_at      TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (storage_type, storage_scope, storage_ref)
);
ALTER TABLE openair_artifact_cleanup_outbox
    ADD COLUMN IF NOT EXISTS storage_scope TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS terminal_at TIMESTAMPTZ;
UPDATE openair_artifact_cleanup_outbox
   SET storage_scope = COALESCE(task_id, '')
 WHERE storage_type = 'local' AND storage_scope = '';
ALTER TABLE openair_artifact_cleanup_outbox
    DROP CONSTRAINT IF EXISTS openair_artifact_cleanup_outbox_storage_type_storage_ref_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_artifact_cleanup_identity
    ON openair_artifact_cleanup_outbox(storage_type, storage_scope, storage_ref);
CREATE INDEX IF NOT EXISTS idx_openair_artifact_cleanup_pending
    ON openair_artifact_cleanup_outbox(state, next_attempt_at, lease_expires_at, cleanup_id);

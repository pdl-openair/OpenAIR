ALTER TABLE openair_dispatch
    ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_openair_dispatch_due_fair
    ON openair_dispatch(status, next_attempt_at NULLS FIRST, updated_at, created_at, dispatch_id)
    WHERE status = 'pending';

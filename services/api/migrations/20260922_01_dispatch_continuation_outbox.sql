CREATE TABLE IF NOT EXISTS openair_dispatch_continuation (
    dispatch_id TEXT PRIMARY KEY REFERENCES openair_dispatch(dispatch_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    execution_decision_id TEXT NOT NULL,
    execution_namespace TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    last_error_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT openair_dispatch_continuation_status_check
        CHECK (status IN ('pending', 'claimed', 'completed')),
    CONSTRAINT openair_dispatch_continuation_attempt_count_check
        CHECK (attempt_count >= 0),
    CONSTRAINT openair_dispatch_continuation_lease_check
        CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_openair_dispatch_continuation_due
    ON openair_dispatch_continuation(
        execution_namespace, status, next_attempt_at, updated_at, dispatch_id
    )
    WHERE status IN ('pending', 'claimed');

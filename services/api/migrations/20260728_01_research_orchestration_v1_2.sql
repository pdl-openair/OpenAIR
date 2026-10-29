DO $$
DECLARE
    revision_data_type TEXT;
    revision_nullable TEXT;
BEGIN
    SELECT data_type, is_nullable
      INTO revision_data_type, revision_nullable
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = 'openair_tasks'
       AND column_name = 'revision';

    IF revision_data_type IS NULL THEN
        RAISE EXCEPTION 'openair_tasks.revision must be provided by 20260720_01';
    END IF;
    IF revision_data_type <> 'bigint' OR revision_nullable <> 'NO' THEN
        RAISE EXCEPTION 'openair_tasks.revision must be BIGINT NOT NULL';
    END IF;
END $$;

CREATE TABLE openair_dispatch (
    dispatch_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE RESTRICT,
    execution_decision_id TEXT NOT NULL,
    work_unit_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    authorization_task_revision BIGINT NULL,
    remote_run_id TEXT NULL,
    next_attempt_at TIMESTAMPTZ NULL,
    last_error_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (length(btrim(dispatch_id)) > 0),
    CHECK (length(btrim(task_id)) > 0),
    CHECK (length(btrim(execution_decision_id)) > 0),
    CHECK (length(btrim(work_unit_id)) > 0),
    CHECK (length(btrim(idempotency_key)) > 0),
    CHECK (status IN (
        'pending', 'claimed', 'submission_authorized', 'submitted',
        'suppressed', 'submission_unknown', 'failed'
    )),
    CHECK (attempt_count >= 0),
    CHECK (authorization_task_revision IS NULL OR authorization_task_revision >= 0),
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CHECK (lease_owner IS NULL OR length(btrim(lease_owner)) > 0),
    CHECK (remote_run_id IS NULL OR length(btrim(remote_run_id)) > 0),
    CHECK (last_error_code IS NULL OR length(btrim(last_error_code)) > 0),
    CHECK (updated_at >= created_at)
);

CREATE INDEX idx_openair_dispatch_claim
    ON openair_dispatch(status, next_attempt_at, created_at, dispatch_id)
    WHERE status = 'pending';

CREATE INDEX idx_openair_dispatch_lease_expiry
    ON openair_dispatch(lease_expires_at, dispatch_id)
    WHERE status = 'claimed';

CREATE INDEX idx_openair_dispatch_task
    ON openair_dispatch(task_id, status, created_at DESC, dispatch_id);

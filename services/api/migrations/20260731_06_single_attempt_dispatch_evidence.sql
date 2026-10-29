ALTER TABLE openair_dispatch
    ADD COLUMN submission_mode TEXT NOT NULL DEFAULT 'durable_idempotent',
    ADD COLUMN external_action_started_at TIMESTAMPTZ NULL;

ALTER TABLE openair_dispatch
    ADD CONSTRAINT openair_dispatch_submission_mode_check
        CHECK (submission_mode IN ('durable_idempotent', 'single_attempt_no_replay')),
    ADD CONSTRAINT openair_dispatch_action_start_requires_authorization_check
        CHECK (external_action_started_at IS NULL OR authorization_task_revision IS NOT NULL);

CREATE INDEX idx_openair_dispatch_single_attempt_recovery
    ON openair_dispatch(status, external_action_started_at, lease_expires_at, dispatch_id)
    WHERE submission_mode = 'single_attempt_no_replay'
      AND status = 'submission_authorized';

LOCK TABLE openair_dispatch IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM openair_dispatch
         WHERE submission_mode <> 'durable_idempotent'
            OR external_action_started_at IS NOT NULL
         LIMIT 1
    ) THEN
        RAISE EXCEPTION 'refusing to drop non-empty single-attempt dispatch evidence';
    END IF;
END
$$;

DROP INDEX idx_openair_dispatch_single_attempt_recovery;
ALTER TABLE openair_dispatch
    DROP CONSTRAINT openair_dispatch_action_start_requires_authorization_check,
    DROP CONSTRAINT openair_dispatch_submission_mode_check,
    DROP COLUMN external_action_started_at,
    DROP COLUMN submission_mode;

DELETE FROM openair_schema_migrations
 WHERE version = '20260731_06_single_attempt_dispatch_evidence.sql';

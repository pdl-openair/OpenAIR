-- Manual rollback for T1. Refuse lossy rollback when the new authority has facts
-- that cannot be represented by the old one-provider-submission-per-call model.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM openair_provider_http_attempts
         WHERE parent_kind <> 'capability_call'
            OR capability_call_id IS NULL
            OR attempt_number <> 1 AND evidence_kind = 'provider_submission'
    ) THEN
        RAISE EXCEPTION 'cannot rollback physical execution truth with new-format evidence present';
    END IF;
END $$;

DROP INDEX IF EXISTS idx_provider_http_attempts_state;
DROP INDEX IF EXISTS idx_provider_http_attempts_parent_operation;
DROP INDEX IF EXISTS uq_provider_http_attempts_parent_physical_ordinal;

ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_transport_outcome_check;
ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_transport_outcome_check
        CHECK (transport_outcome IN (
            'in_flight', 'response_received', 'provider_http_error',
            'transport_uncertain', 'transport_not_submitted'
        ));

ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_execution_state_check,
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_logical_attempt_positive,
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_parent_kind_check;

ALTER TABLE openair_provider_http_attempts
    DROP COLUMN IF EXISTS submission_proven,
    DROP COLUMN IF EXISTS io_started,
    DROP COLUMN IF EXISTS originating_authority,
    DROP COLUMN IF EXISTS execution_stage,
    DROP COLUMN IF EXISTS execution_reason,
    DROP COLUMN IF EXISTS execution_state,
    DROP COLUMN IF EXISTS model_id,
    DROP COLUMN IF EXISTS provider_id,
    DROP COLUMN IF EXISTS logical_attempt_ordinal,
    DROP COLUMN IF EXISTS operation_identity,
    DROP COLUMN IF EXISTS parent_id,
    DROP COLUMN IF EXISTS parent_kind;

ALTER TABLE openair_provider_http_attempts
    ALTER COLUMN capability_call_id SET NOT NULL;
ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_capability_call_id_attempt_number_key
        UNIQUE (capability_call_id, attempt_number);
CREATE UNIQUE INDEX uq_provider_http_attempts_provider_submission
    ON openair_provider_http_attempts(capability_call_id)
    WHERE evidence_kind = 'provider_submission';

DELETE FROM openair_schema_migrations
 WHERE version = '20260928_01_physical_execution_truth.sql';

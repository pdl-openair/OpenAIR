-- T1: one durable physical execution truth for every production Provider submission.
-- Existing capability-call rows are preserved and backfilled as the first parent kind.

ALTER TABLE openair_provider_http_attempts
    ALTER COLUMN capability_call_id DROP NOT NULL;

ALTER TABLE openair_provider_http_attempts
    ADD COLUMN IF NOT EXISTS parent_kind TEXT,
    ADD COLUMN IF NOT EXISTS parent_id TEXT,
    ADD COLUMN IF NOT EXISTS operation_identity TEXT,
    ADD COLUMN IF NOT EXISTS logical_attempt_ordinal INTEGER,
    ADD COLUMN IF NOT EXISTS provider_id TEXT,
    ADD COLUMN IF NOT EXISTS model_id TEXT,
    ADD COLUMN IF NOT EXISTS execution_state TEXT,
    ADD COLUMN IF NOT EXISTS execution_reason TEXT,
    ADD COLUMN IF NOT EXISTS execution_stage TEXT,
    ADD COLUMN IF NOT EXISTS originating_authority TEXT,
    ADD COLUMN IF NOT EXISTS io_started BOOLEAN,
    ADD COLUMN IF NOT EXISTS submission_proven BOOLEAN;

UPDATE openair_provider_http_attempts
   SET parent_kind = COALESCE(parent_kind, 'capability_call'),
       parent_id = COALESCE(parent_id, capability_call_id),
       operation_identity = COALESCE(
           operation_identity,
           CASE
             WHEN evidence_kind = 'provider_submission' THEN 'provider_submission'
             ELSE 'sub:' || evidence_kind || ':' || COALESCE(adapter_name, 'external') || ':' || substr(request_hash, 1, 16)
           END
       ),
       logical_attempt_ordinal = COALESCE(logical_attempt_ordinal, 1),
       execution_state = COALESCE(
           execution_state,
           CASE transport_outcome
             WHEN 'response_received' THEN 'completed'
             WHEN 'provider_http_error' THEN 'failed'
             WHEN 'transport_not_submitted' THEN 'failed'
             WHEN 'transport_uncertain' THEN 'unknown'
             WHEN 'in_flight' THEN 'unknown'
             ELSE 'unknown'
           END
       ),
       execution_reason = COALESCE(
           execution_reason,
           CASE transport_outcome
             WHEN 'response_received' THEN 'provider_response_received'
             WHEN 'provider_http_error' THEN 'known_provider_failure'
             WHEN 'transport_not_submitted' THEN 'transport_not_submitted'
             WHEN 'transport_uncertain' THEN 'transport_uncertain'
             WHEN 'in_flight' THEN 'historical_in_flight_without_terminal_evidence'
             ELSE NULL
           END
       ),
       execution_stage = COALESCE(
           execution_stage,
           CASE WHEN transport_outcome <> 'in_flight' THEN 'response_observation' END
       ),
       originating_authority = COALESCE(originating_authority, 'capability_call_runtime'),
       io_started = CASE
           WHEN io_started IS NOT NULL THEN io_started
           WHEN transport_outcome = 'in_flight' THEN NULL
           ELSE TRUE
       END,
       submission_proven = COALESCE(
           submission_proven,
           CASE transport_outcome
             WHEN 'response_received' THEN TRUE
             WHEN 'provider_http_error' THEN TRUE
             WHEN 'transport_not_submitted' THEN FALSE
             ELSE NULL
           END
       );

ALTER TABLE openair_provider_http_attempts
    ALTER COLUMN parent_kind SET NOT NULL,
    ALTER COLUMN parent_id SET NOT NULL,
    ALTER COLUMN operation_identity SET NOT NULL,
    ALTER COLUMN logical_attempt_ordinal SET NOT NULL,
    ALTER COLUMN execution_state SET NOT NULL,
    ALTER COLUMN originating_authority SET NOT NULL;


ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_transport_outcome_check;
ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_transport_outcome_check
        CHECK (transport_outcome IN (
            'in_flight', 'response_received', 'provider_http_error',
            'transport_not_submitted', 'transport_uncertain', 'provider_cancelled'
        ));

ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_parent_kind_check
        CHECK (parent_kind IN ('capability_call', 'research_start', 'model_job', 'agent_session', 'system_probe')),
    ADD CONSTRAINT openair_provider_http_attempts_logical_attempt_positive
        CHECK (logical_attempt_ordinal >= 1),
    ADD CONSTRAINT openair_provider_http_attempts_execution_state_check
        CHECK (execution_state IN ('running', 'completed', 'failed', 'cancelled', 'unknown'));

-- The old C1 uniqueness encoded logical-call identity as physical-attempt identity.
-- Physical submissions now get a new monotonic ordinal under their durable parent.
DROP INDEX IF EXISTS uq_provider_http_attempts_provider_submission;
ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_capability_call_id_attempt_number_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_http_attempts_parent_physical_ordinal
    ON openair_provider_http_attempts(parent_kind, parent_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_provider_http_attempts_parent_operation
    ON openair_provider_http_attempts(parent_kind, parent_id, operation_identity, started_at, attempt_number);
CREATE INDEX IF NOT EXISTS idx_provider_http_attempts_state
    ON openair_provider_http_attempts(execution_state, started_at, attempt_id);

CREATE TABLE openair_external_execution_checkpoints (
    capability_call_id TEXT PRIMARY KEY,
    dispatch_id TEXT NOT NULL UNIQUE REFERENCES openair_dispatch(dispatch_id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL UNIQUE,
    execution_kind TEXT NOT NULL CHECK (
        execution_kind IN ('provider_http', 'external_adapter', 'skill_script', 'direct_skill_runtime')
    ),
    provider_id TEXT NOT NULL,
    adapter TEXT NOT NULL,
    request_hash CHAR(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    phase TEXT NOT NULL DEFAULT 'prepared'
        CHECK (phase IN ('prepared', 'submitted', 'response_received', 'canonical_completed')),
    response_kind TEXT NULL CHECK (
        response_kind IS NULL OR response_kind IN (
            'provider_http_response', 'adapter_result', 'skill_script_result', 'dispatch_completed'
        )
    ),
    response_payload BYTEA NULL,
    response_hash CHAR(64) NULL,
    response_http_status INTEGER NULL CHECK (response_http_status BETWEEN 100 AND 599),
    prepared_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    submitted_at TIMESTAMPTZ NULL,
    response_received_at TIMESTAMPTZ NULL,
    canonical_completed_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT openair_external_checkpoint_response_complete CHECK (
        (response_kind IS NULL AND response_payload IS NULL AND response_hash IS NULL)
        OR
        (response_kind IS NOT NULL AND response_payload IS NOT NULL
         AND response_hash ~ '^[0-9a-f]{64}$')
    ),
    CONSTRAINT openair_external_checkpoint_phase_evidence CHECK (
        (phase = 'prepared' AND submitted_at IS NULL AND response_received_at IS NULL)
        OR
        (phase = 'submitted' AND submitted_at IS NOT NULL AND response_received_at IS NULL)
        OR
        (phase IN ('response_received', 'canonical_completed')
         AND submitted_at IS NOT NULL AND response_received_at IS NOT NULL
         AND response_payload IS NOT NULL)
    )
);

CREATE INDEX idx_openair_external_execution_checkpoint_recovery
    ON openair_external_execution_checkpoints(phase, updated_at, capability_call_id)
    WHERE phase <> 'canonical_completed';

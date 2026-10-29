CREATE TABLE openair_provider_http_attempts (
    attempt_id TEXT PRIMARY KEY,
    -- Durable calls live in openair_execution_capability_calls; legacy trial
    -- calls live in openair_tasks.payload. The repository validates either
    -- owner before insert, so evidence can cover both without an invalid FK.
    capability_call_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number = 1),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    provider_endpoint_identity TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ NULL,
    http_status INTEGER NULL CHECK (http_status BETWEEN 100 AND 599),
    provider_request_id TEXT NULL,
    provider_response_id TEXT NULL,
    response_hash TEXT NULL CHECK (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'),
    transport_outcome TEXT NOT NULL DEFAULT 'in_flight'
        CHECK (transport_outcome IN (
            'in_flight',
            'response_received',
            'provider_http_error',
            'transport_uncertain'
        )),
    UNIQUE (capability_call_id),
    UNIQUE (capability_call_id, attempt_number)
);

CREATE INDEX idx_openair_provider_http_attempts_started
    ON openair_provider_http_attempts(started_at, attempt_id);

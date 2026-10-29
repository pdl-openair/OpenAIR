-- Freeze-safe Provider execution evidence for durable LLM capability calls.
-- Stores only the resolved, credential-free execution policy; request/response
-- bodies remain excluded from durable evidence.

ALTER TABLE openair_provider_http_attempts
    ADD COLUMN IF NOT EXISTS resolved_execution_policy JSONB NULL
        CHECK (
            resolved_execution_policy IS NULL
            OR jsonb_typeof(resolved_execution_policy) = 'object'
        );

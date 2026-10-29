-- Bounded, body-free diagnostics for provider HTTP rejections.
ALTER TABLE openair_provider_http_attempts
    ADD COLUMN IF NOT EXISTS rejection_diagnostics JSONB NULL;

ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_rejection_diagnostics_object
    CHECK (
        rejection_diagnostics IS NULL
        OR jsonb_typeof(rejection_diagnostics) = 'object'
    );

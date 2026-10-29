UPDATE openair_provider_http_attempts
   SET transport_outcome = 'transport_uncertain'
 WHERE transport_outcome = 'transport_not_submitted';

ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_transport_outcome_check;

ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_transport_outcome_check
        CHECK (transport_outcome IN (
            'in_flight',
            'response_received',
            'provider_http_error',
            'transport_uncertain'
        ));

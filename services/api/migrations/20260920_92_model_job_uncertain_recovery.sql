-- P1: uncertain Model Job execution is a durable fact, never replay authority.
--
-- A leased model-backed operation may have reached the Provider before the local
-- worker loses its lease or fails to persist the queue terminal state. Preserve
-- that uncertainty explicitly. The queue may only converge to succeeded when
-- existing durable business state mechanically proves the requested operation
-- already completed.

ALTER TABLE openair_model_jobs
    DROP CONSTRAINT IF EXISTS openair_model_jobs_state_check;

ALTER TABLE openair_model_jobs
    ADD CONSTRAINT openair_model_jobs_state_check
    CHECK (state IN (
        'queued',
        'leased',
        'succeeded',
        'blocked',
        'failed',
        'external_result_unknown'
    ));

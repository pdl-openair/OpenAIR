-- Guarded rollback for P1 Model Job uncertain execution recovery.
-- Never erase an already-persisted uncertainty fact.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM openair_model_jobs
         WHERE state = 'external_result_unknown'
    ) THEN
        RAISE EXCEPTION
            'cannot roll back model-job uncertainty contract: external_result_unknown rows exist';
    END IF;
END $$;

ALTER TABLE openair_model_jobs
    DROP CONSTRAINT IF EXISTS openair_model_jobs_state_check;

ALTER TABLE openair_model_jobs
    ADD CONSTRAINT openair_model_jobs_state_check
    CHECK (state IN ('queued', 'leased', 'succeeded', 'blocked', 'failed'));

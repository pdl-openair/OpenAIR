-- T02 rollback.
--
-- Refuse to restore the obsolete stricter constraint while any active attempt
-- already contains response evidence. Silently discarding that evidence would
-- violate the persisted-call-fact contract.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM research_start_attempts
         WHERE state = 'started'
           AND result_payload IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'cannot restore research_start_attempts_check: started attempts contain result_payload';
    END IF;
END $$;

ALTER TABLE research_start_attempts
    DROP CONSTRAINT research_start_attempts_check;

ALTER TABLE research_start_attempts
    ADD CONSTRAINT research_start_attempts_check
    CHECK (
        (
            state = 'started'
            AND finished_at IS NULL
            AND result_payload IS NULL
        )
        OR
        (
            state <> 'started'
            AND finished_at IS NOT NULL
        )
    );

-- T02: preserve Provider response evidence while a Research Start attempt is still active.
--
-- The runtime records protected responseEvidence before decoding/finalizing the
-- Planner response. At that point the attempt is still state='started', so
-- finished_at must remain NULL. The previous constraint incorrectly also
-- required result_payload to be NULL, making durable response evidence
-- impossible to persist.
--
-- Keep the lifecycle invariant:
--   started     -> finished_at IS NULL
--   non-started -> finished_at IS NOT NULL
-- result_payload is intentionally not part of attempt terminality.

ALTER TABLE research_start_attempts
    DROP CONSTRAINT research_start_attempts_check;

ALTER TABLE research_start_attempts
    ADD CONSTRAINT research_start_attempts_check
    CHECK (
        (state = 'started' AND finished_at IS NULL)
        OR
        (state <> 'started' AND finished_at IS NOT NULL)
    );

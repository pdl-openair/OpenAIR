-- The legacy Router phase has been removed. Existing rows remain valid
-- planning evidence only when they already belong to the Planner phase.

BEGIN;

DELETE FROM research_start_attempts
 WHERE phase <> 'research_planner';

ALTER TABLE research_start_attempts
    DROP CONSTRAINT IF EXISTS research_start_attempts_phase_check;

ALTER TABLE research_start_attempts
    ADD CONSTRAINT research_start_attempts_phase_check
    CHECK (phase = 'research_planner');

COMMIT;

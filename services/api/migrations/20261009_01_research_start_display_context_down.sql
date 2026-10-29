ALTER TABLE openair_tasks DROP COLUMN IF EXISTS title_is_automatic;
ALTER TABLE research_start_sessions
    DROP COLUMN IF EXISTS display_title,
    DROP COLUMN IF EXISTS title_attempted_at,
    DROP COLUMN IF EXISTS title_finished_at;

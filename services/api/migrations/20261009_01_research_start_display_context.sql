-- Optional display metadata. No planning/execution state or prerequisite changes.
ALTER TABLE research_start_sessions
    ADD COLUMN IF NOT EXISTS display_title TEXT,
    ADD COLUMN IF NOT EXISTS title_attempted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS title_finished_at TIMESTAMPTZ;
-- Existing names are protected by default, including names equal to 新研究.
ALTER TABLE openair_tasks ADD COLUMN IF NOT EXISTS title_is_automatic BOOLEAN NOT NULL DEFAULT FALSE;

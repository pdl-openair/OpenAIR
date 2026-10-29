-- Append-only user-facing thinking for the workbench collaboration panel.
-- Already written entries stay on the session; the open entry may grow.

ALTER TABLE research_start_sessions
    ADD COLUMN thinking_transcript JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE research_start_sessions
    ADD CONSTRAINT research_start_sessions_thinking_transcript_check
    CHECK (jsonb_typeof(thinking_transcript) = 'array');

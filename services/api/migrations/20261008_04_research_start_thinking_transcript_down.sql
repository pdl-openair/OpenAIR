ALTER TABLE research_start_sessions
    DROP CONSTRAINT IF EXISTS research_start_sessions_thinking_transcript_check;

ALTER TABLE research_start_sessions
    DROP COLUMN IF EXISTS thinking_transcript;

-- Store every ResearchStart Provider response directly on its authoritative
-- attempt row before parsing or compilation.

ALTER TABLE research_start_attempts
    ADD COLUMN response_body BYTEA NULL;

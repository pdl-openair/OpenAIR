-- Rolling back would discard captured Provider responses.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM research_start_attempts WHERE response_body IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'cannot remove research start response body while captured responses exist';
    END IF;
END $$;

ALTER TABLE research_start_attempts
    DROP COLUMN response_body;

DELETE FROM openair_schema_migrations
 WHERE version = '20261008_02_research_start_response_body.sql';

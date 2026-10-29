BEGIN;

LOCK TABLE research_start_sessions IN ACCESS EXCLUSIVE MODE;
LOCK TABLE research_start_requests IN ACCESS EXCLUSIVE MODE;
LOCK TABLE research_start_attempts IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM research_start_attempts LIMIT 1)
       OR EXISTS (SELECT 1 FROM research_start_requests LIMIT 1)
       OR EXISTS (SELECT 1 FROM research_start_sessions LIMIT 1) THEN
        RAISE EXCEPTION 'refusing to drop non-empty durable research-start tables';
    END IF;
END $$;

DROP TABLE research_start_attempts;
DROP TABLE research_start_requests;
DROP TABLE research_start_sessions;

DELETE FROM openair_schema_migrations
 WHERE version = '20260725_02_durable_research_start.sql';

COMMIT;

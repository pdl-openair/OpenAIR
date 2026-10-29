BEGIN;

LOCK TABLE openair_dispatch IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM openair_dispatch LIMIT 1) THEN
        RAISE EXCEPTION 'refusing to drop non-empty openair_dispatch';
    END IF;
END $$;

DROP TABLE openair_dispatch;

DELETE FROM openair_schema_migrations
 WHERE version = '20260728_01_research_orchestration_v1_2.sql';

COMMIT;

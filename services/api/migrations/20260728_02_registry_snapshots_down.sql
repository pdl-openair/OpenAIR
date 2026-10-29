BEGIN;

LOCK TABLE openair_registry_snapshots IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM openair_registry_snapshots LIMIT 1) THEN
        RAISE EXCEPTION 'refusing to remove non-empty openair_registry_snapshots archive';
    END IF;
END $$;

DROP TRIGGER openair_registry_snapshots_reject_update_delete ON openair_registry_snapshots;
DROP TRIGGER openair_registry_snapshots_reject_truncate ON openair_registry_snapshots;
DROP TABLE openair_registry_snapshots;
DROP FUNCTION openair_registry_snapshots_reject_mutation();

DELETE FROM openair_schema_migrations
WHERE version = '20260728_02_registry_snapshots.sql';

COMMIT;

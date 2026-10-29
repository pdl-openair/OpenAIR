BEGIN;

LOCK TABLE openair_artifact_lineage_audit,
           openair_artifact_lineage,
           openair_capability_call_lineage,
           openair_work_unit_lineage
    IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM openair_artifact_lineage_audit LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_artifact_lineage LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_capability_call_lineage LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_work_unit_lineage LIMIT 1) THEN
        RAISE EXCEPTION 'refusing to drop non-empty artifact lineage audit tables';
    END IF;
END $$;

DROP TABLE openair_artifact_lineage_audit;
DROP TABLE openair_artifact_lineage;
DROP TABLE openair_capability_call_lineage;
DROP TABLE openair_work_unit_lineage;

DELETE FROM openair_schema_migrations
 WHERE version = '20260730_05_artifact_lineage.sql';

COMMIT;

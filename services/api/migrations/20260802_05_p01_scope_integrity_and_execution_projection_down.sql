-- Manual rollback for 20260802_05_p01_scope_integrity_and_execution_projection.sql.

DROP TRIGGER IF EXISTS trg_openair_validate_recovery_execution_projection
    ON openair_recovery_execution_projections;
DROP FUNCTION IF EXISTS openair_validate_recovery_execution_projection();
DROP INDEX IF EXISTS idx_openair_recovery_execution_projections_source;
DROP TABLE IF EXISTS openair_recovery_execution_projections;

DROP INDEX IF EXISTS uq_openair_recovery_successor_run;
DROP INDEX IF EXISTS uq_openair_recovery_successor_per_source_revision;

ALTER TABLE openair_execution_runs
    DROP CONSTRAINT IF EXISTS openair_execution_runs_task_recovery_scope_fk;

DELETE FROM openair_schema_migrations
 WHERE version = '20260802_05_p01_scope_integrity_and_execution_projection.sql';

-- Manual rollback for 20260802_04_p01_task_recovery.sql.
-- Later Recovery migrations must be rolled back first.

DROP TRIGGER IF EXISTS trg_openair_guard_task_recovery_request_update
    ON openair_task_recovery_requests;
DROP FUNCTION IF EXISTS openair_guard_task_recovery_request_update();
DROP TRIGGER IF EXISTS trg_openair_validate_task_recovery_request_insert
    ON openair_task_recovery_requests;
DROP FUNCTION IF EXISTS openair_validate_task_recovery_request_insert();
DROP INDEX IF EXISTS idx_openair_task_recovery_requests_task;
DROP TABLE IF EXISTS openair_task_recovery_requests;

ALTER TABLE openair_execution_runs
    DROP CONSTRAINT IF EXISTS uq_openair_execution_runs_recovery_scope;

ALTER TABLE openair_tasks
    DROP CONSTRAINT IF EXISTS uq_openair_tasks_recovery_scope;

DELETE FROM openair_schema_migrations
 WHERE version = '20260802_04_p01_task_recovery.sql';

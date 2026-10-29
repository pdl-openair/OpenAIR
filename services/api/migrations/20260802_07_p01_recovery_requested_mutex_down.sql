-- Manual rollback for 20260802_07_p01_recovery_requested_mutex.sql.

DROP INDEX IF EXISTS uq_openair_task_recovery_requested_mutex;

DELETE FROM openair_schema_migrations
 WHERE version = '20260802_07_p01_recovery_requested_mutex.sql';

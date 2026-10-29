-- Manual, reviewed rollback for 20260720_01_ib01_durable_execution.sql.
-- Do not run automatically: it intentionally removes durable execution data.
DROP TABLE IF EXISTS openair_execution_artifact_associations;
DROP TABLE IF EXISTS openair_execution_capability_calls;
DROP TABLE IF EXISTS openair_execution_idempotency;
DROP TABLE IF EXISTS openair_artifact_uploads;
DROP TABLE IF EXISTS openair_execution_callback_rejections;
DROP TABLE IF EXISTS openair_execution_run_events;
DROP TABLE IF EXISTS openair_execution_runs;
ALTER TABLE openair_artifacts DROP COLUMN IF EXISTS source_execution_run_id;
ALTER TABLE openair_tasks DROP COLUMN IF EXISTS revision;
DELETE FROM openair_schema_migrations WHERE version = '20260720_01_ib01_durable_execution.sql';

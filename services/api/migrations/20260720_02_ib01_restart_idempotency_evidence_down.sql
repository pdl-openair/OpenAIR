DROP INDEX IF EXISTS idx_openair_execution_start_attempts_task;
DROP TABLE IF EXISTS openair_execution_start_attempts;
ALTER TABLE openair_artifacts DROP COLUMN IF EXISTS source_mode;

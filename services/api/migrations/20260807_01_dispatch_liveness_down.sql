DROP INDEX IF EXISTS idx_openair_dispatch_due_fair;
ALTER TABLE openair_dispatch DROP COLUMN IF EXISTS last_failure_at;

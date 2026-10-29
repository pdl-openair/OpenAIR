DROP TRIGGER IF EXISTS openair_dispatch_execution_namespace_guard ON openair_dispatch;
DROP FUNCTION IF EXISTS openair_guard_dispatch_execution_namespace();
DROP INDEX IF EXISTS idx_openair_dispatch_execution_namespace_claim;
ALTER TABLE openair_dispatch
    DROP CONSTRAINT IF EXISTS openair_dispatch_execution_namespace_check;
ALTER TABLE openair_dispatch
    DROP COLUMN IF EXISTS execution_namespace;

ALTER TABLE openair_dispatch
    ADD COLUMN IF NOT EXISTS execution_namespace TEXT NULL;

ALTER TABLE openair_dispatch
    DROP CONSTRAINT IF EXISTS openair_dispatch_execution_namespace_check;
ALTER TABLE openair_dispatch
    ADD CONSTRAINT openair_dispatch_execution_namespace_check
    CHECK (execution_namespace IS NULL OR length(btrim(execution_namespace)) > 0);

CREATE INDEX IF NOT EXISTS idx_openair_dispatch_execution_namespace_claim
    ON openair_dispatch(execution_namespace, status, next_attempt_at NULLS FIRST, created_at, dispatch_id)
    WHERE execution_namespace IS NOT NULL
      AND status IN ('pending', 'claimed', 'submission_authorized');

CREATE OR REPLACE FUNCTION openair_guard_dispatch_execution_namespace()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    caller_namespace TEXT := current_setting('openair.execution_namespace', true);
BEGIN
    -- Rows created before this protocol remain legacy-compatible. Every new
    -- canonical Dispatch is required by application invariants to carry a namespace.
    IF OLD.execution_namespace IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.execution_namespace IS DISTINCT FROM OLD.execution_namespace THEN
        RAISE EXCEPTION 'dispatch execution_namespace is immutable';
    END IF;

    -- Owner-driven task deletion is an administrative stop, not execution
    -- ownership. It remains allowed so a stale namespace cannot prevent cleanup.
    IF NEW.status = 'suppressed' THEN
        RETURN NEW;
    END IF;

    -- Deliberately broader than claim: legacy workers can also settle resolver
    -- failures or take over expired authorized submissions without the new claim SQL.
    -- A foreign/no-namespace connection therefore cannot mutate any namespaced
    -- dispatch lifecycle fact.
    IF caller_namespace IS DISTINCT FROM OLD.execution_namespace THEN
        RETURN NULL;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS openair_dispatch_execution_namespace_guard ON openair_dispatch;
CREATE TRIGGER openair_dispatch_execution_namespace_guard
BEFORE UPDATE ON openair_dispatch
FOR EACH ROW
EXECUTE FUNCTION openair_guard_dispatch_execution_namespace();

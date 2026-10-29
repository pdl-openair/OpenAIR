-- Shared Runtime source/version fence for durable Research Start.
--
-- New API-created requests carry one deterministic backend source identity.
-- PostgreSQL, not worker code, enforces every queued -> leased transition so an
-- already-running pre-fence worker cannot claim either new fenced requests or
-- unversioned legacy queue rows after this migration is active.

ALTER TABLE research_start_requests
    ADD COLUMN IF NOT EXISTS runtime_source_version TEXT NULL;

ALTER TABLE research_start_requests
    DROP CONSTRAINT IF EXISTS research_start_requests_runtime_source_version_check;

ALTER TABLE research_start_requests
    ADD CONSTRAINT research_start_requests_runtime_source_version_check
    CHECK (
        runtime_source_version IS NULL
        OR runtime_source_version ~ '^[0-9a-f]{64}$'
    );

CREATE INDEX IF NOT EXISTS idx_research_start_requests_source_claim
    ON research_start_requests(runtime_source_version, state, created_at, request_id)
    WHERE state = 'queued';

CREATE OR REPLACE FUNCTION enforce_research_start_runtime_source_fence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    worker_source_version TEXT;
BEGIN
    IF OLD.state = 'queued' AND NEW.state = 'leased' THEN
        worker_source_version := NULLIF(
            current_setting('openair.runtime_source_version', true),
            ''
        );
        IF NEW.runtime_source_version IS NULL
           OR worker_source_version IS DISTINCT FROM NEW.runtime_source_version THEN
            RAISE EXCEPTION 'research_start_runtime_source_mismatch'
                USING ERRCODE = 'P0001',
                      DETAIL = format(
                          'required=%s worker=%s',
                          COALESCE(NEW.runtime_source_version, '<unversioned>'),
                          COALESCE(worker_source_version, '<unset>')
                      );
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_research_start_runtime_source_fence
    ON research_start_requests;

CREATE TRIGGER trg_research_start_runtime_source_fence
BEFORE UPDATE OF state, lease_owner, lease_epoch, lease_expires_at
ON research_start_requests
FOR EACH ROW
EXECUTE FUNCTION enforce_research_start_runtime_source_fence();

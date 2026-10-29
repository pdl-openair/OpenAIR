DROP TRIGGER IF EXISTS trg_research_start_runtime_source_fence
    ON research_start_requests;

DROP FUNCTION IF EXISTS enforce_research_start_runtime_source_fence();

DROP INDEX IF EXISTS idx_research_start_requests_source_claim;

ALTER TABLE research_start_requests
    DROP CONSTRAINT IF EXISTS research_start_requests_runtime_source_version_check;

ALTER TABLE research_start_requests
    DROP COLUMN IF EXISTS runtime_source_version;

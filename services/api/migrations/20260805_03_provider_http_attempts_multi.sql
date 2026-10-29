-- Multi-row provider HTTP evidence per capability_call.
--
-- The C1 contract ("exactly one Provider LLM submission per capability_call")
-- is preserved via a partial unique index on the legacy path. The new
-- multi-call path (e.g. materials MCP fan-out under one capability_call)
-- stores multiple rows discriminated by evidence_kind = 'mcp_call'.
--
-- Naming notes: Postgres auto-names the constraints we drop. The CHECK on
-- attempt_number=1 becomes <table>_attempt_number_check; the table-level
-- UNIQUE(capability_call_id) becomes <table>_capability_call_id_key. Both
-- names are deterministic, but DROP CONSTRAINT IF EXISTS tolerates drift.

ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_attempt_number_check;
ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_capability_call_id_key;

-- Allow any positive attempt_number (1 = legacy provider_submission, >1 = sub-call evidence).
ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_attempt_number_positive
        CHECK (attempt_number >= 1);

-- Discriminate C1 LLM submissions from MCP / sub-call HTTP traffic.
ALTER TABLE openair_provider_http_attempts
    ADD COLUMN IF NOT EXISTS evidence_kind TEXT NOT NULL DEFAULT 'provider_submission'
        CHECK (evidence_kind IN ('provider_submission', 'mcp_call', 'sub_call'));

-- Optional MCP / adapter name for sub-call rows (NULL for legacy provider submissions).
ALTER TABLE openair_provider_http_attempts
    ADD COLUMN IF NOT EXISTS adapter_name TEXT NULL;

-- Preserve the C1 invariant: at most one provider_submission per capability_call_id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_http_attempts_provider_submission
    ON openair_provider_http_attempts (capability_call_id)
    WHERE evidence_kind = 'provider_submission';

-- Sub-call rows are addressed by (capability_call_id, attempt_number); the
-- pre-existing UNIQUE (capability_call_id, attempt_number) constraint still
-- guards against exact-duplicate inserts.

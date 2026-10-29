-- Manual rollback for 20260805_03_provider_http_attempts_multi.sql only.

DROP INDEX IF EXISTS uq_provider_http_attempts_provider_submission;

ALTER TABLE openair_provider_http_attempts
    DROP COLUMN IF EXISTS adapter_name;

-- Drop the kind CHECK first, then the column (Postgres drops the check with the column).
ALTER TABLE openair_provider_http_attempts
    DROP COLUMN IF EXISTS evidence_kind;

ALTER TABLE openair_provider_http_attempts
    DROP CONSTRAINT IF EXISTS openair_provider_http_attempts_attempt_number_positive;

-- Restore the original C1 constraints: attempt_number must be 1 and each
-- capability_call_id has at most one row.
ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_attempt_number_check
        CHECK (attempt_number = 1);

-- Drop sub-call rows that violate the restored UNIQUE(capability_call_id)
-- before re-adding the constraint.
--
-- ROLLBACK IS LOSSY: every non-provider_submission row (mcp_call / sub_call)
-- is discarded. The original C1 table only knew about Provider LLM
-- submissions; sub-call evidence cannot survive the constraint restoration.
--
-- For each capability_call_id, keep at most ONE provider_submission row
-- (the lowest attempt_id). If a call has only sub-call rows (no
-- provider_submission), all its rows are deleted — otherwise the lowest
-- sub-call row would survive and masquerade as a C1 row.
DO $$
DECLARE
    discarded_subcall_count integer;
    discarded_dupe_count    integer;
BEGIN
    -- Count sub-call rows about to be deleted (for operator visibility).
    SELECT COUNT(*) INTO discarded_subcall_count
      FROM openair_provider_http_attempts
     WHERE evidence_kind <> 'provider_submission';

    -- Delete every row that is NOT a provider_submission first.
    DELETE FROM openair_provider_http_attempts
     WHERE evidence_kind <> 'provider_submission';

    -- Among surviving (provider_submission) rows, drop duplicates per
    -- capability_call_id keeping the lowest attempt_id. The partial unique
    -- index scoped to provider_submission guarantees at most one per call,
    -- but be defensive in case the index was added after duplicates crept in.
    SELECT COUNT(*) INTO discarded_dupe_count
      FROM openair_provider_http_attempts a
     WHERE a.attempt_id NOT IN (
         SELECT MIN(b.attempt_id)
           FROM openair_provider_http_attempts b
          GROUP BY b.capability_call_id
     );

    DELETE FROM openair_provider_http_attempts a
     WHERE a.attempt_id NOT IN (
         SELECT MIN(b.attempt_id)
           FROM openair_provider_http_attempts b
          GROUP BY b.capability_call_id
     );

    RAISE NOTICE 'rollback 20260805_03: discarded % sub-call row(s) and % duplicate provider_submission row(s)',
        discarded_subcall_count, discarded_dupe_count;
END $$;

ALTER TABLE openair_provider_http_attempts
    ADD CONSTRAINT openair_provider_http_attempts_capability_call_id_key
        UNIQUE (capability_call_id);

DELETE FROM openair_schema_migrations
 WHERE version = '20260805_03_provider_http_attempts_multi.sql';

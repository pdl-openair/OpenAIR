-- Per-Run durable provider HTTP budget (P0-1 External-Action Budget Gate).
-- Schema-only: reserve/CAS/runtime enforcement are out of scope for A1.
-- Fail-closed: refuse if any target object already exists or shape is abnormal.
-- Forbidden: idempotent DDL guards on ADD COLUMN / CREATE TABLE /
--            CREATE INDEX / DROP CONSTRAINT (must use unconditional DDL after preflight).

DO $$
DECLARE
    conflict_count INTEGER := 0;
    sample_call_ids TEXT;
    col_max_exists BOOLEAN;
    col_reserved_exists BOOLEAN;
    constraint_exists BOOLEAN;
    table_exists BOOLEAN;
    idx_uq_exists BOOLEAN;
    idx_run_exists BOOLEAN;
BEGIN
    IF to_regclass('public.openair_execution_runs') IS NULL THEN
        RAISE EXCEPTION
            'p01 external_action_budget preflight failed: baseline table openair_execution_runs does not exist';
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'openair_execution_runs'
           AND column_name = 'max_external_actions'
    ) INTO col_max_exists;

    SELECT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'openair_execution_runs'
           AND column_name = 'external_actions_reserved'
    ) INTO col_reserved_exists;

    IF col_max_exists OR col_reserved_exists THEN
        RAISE EXCEPTION
            'p01 external_action_budget preflight failed: target column(s) already exist (max_external_actions=%, external_actions_reserved=%); refuse modify',
            col_max_exists,
            col_reserved_exists;
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = 'public'
           AND t.relname = 'openair_execution_runs'
           AND c.conname = 'openair_execution_runs_external_actions_reserved_lte_max'
    ) INTO constraint_exists;

    IF constraint_exists THEN
        RAISE EXCEPTION
            'p01 external_action_budget preflight failed: target CHECK openair_execution_runs_external_actions_reserved_lte_max already exists; refuse modify';
    END IF;

    table_exists := to_regclass('public.openair_execution_external_action_reservations') IS NOT NULL;

    IF table_exists THEN
        BEGIN
            SELECT COUNT(*) INTO conflict_count
            FROM (
                SELECT call_id
                  FROM openair_execution_external_action_reservations
                 WHERE attempt_kind = 'initial'
                   AND outcome = 'reserved'
                 GROUP BY call_id
                HAVING COUNT(*) > 1
            ) conflicts;
        EXCEPTION
            WHEN undefined_column OR undefined_table THEN
                RAISE EXCEPTION
                    'p01 external_action_budget preflight failed: reservation table openair_execution_external_action_reservations already exists with abnormal shape; refuse modify';
        END;

        IF conflict_count > 0 THEN
            SELECT string_agg(sample_id, ', ' ORDER BY sample_id) INTO sample_call_ids
            FROM (
                SELECT left(md5(call_id), 12) AS sample_id
                  FROM openair_execution_external_action_reservations
                 WHERE attempt_kind = 'initial'
                   AND outcome = 'reserved'
                 GROUP BY call_id
                HAVING COUNT(*) > 1
                 ORDER BY call_id
                 LIMIT 5
            ) samples;

            RAISE EXCEPTION
                'p01 external_action_budget preflight failed: reservation table already exists with % conflicting call_id group(s) for uq_openair_external_action_initial_reserved; sample call_id(s): %; refuse auto-dedupe; resolve manually before retry',
                conflict_count,
                COALESCE(sample_call_ids, '');
        END IF;

        RAISE EXCEPTION
            'p01 external_action_budget preflight failed: reservation table openair_execution_external_action_reservations already exists; refuse modify';
    END IF;

    SELECT EXISTS (
        SELECT 1
          FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname = 'uq_openair_external_action_initial_reserved'
    ) INTO idx_uq_exists;

    SELECT EXISTS (
        SELECT 1
          FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname = 'idx_openair_external_action_reservations_run'
    ) INTO idx_run_exists;

    IF idx_uq_exists OR idx_run_exists THEN
        RAISE EXCEPTION
            'p01 external_action_budget preflight failed: target index(es) already exist (uq_openair_external_action_initial_reserved=%, idx_openair_external_action_reservations_run=%); refuse modify',
            idx_uq_exists,
            idx_run_exists;
    END IF;
END
$$;

ALTER TABLE openair_execution_runs
    ADD COLUMN max_external_actions INTEGER NULL
        CHECK (max_external_actions IS NULL OR max_external_actions > 0),
    ADD COLUMN external_actions_reserved INTEGER NOT NULL DEFAULT 0
        CHECK (external_actions_reserved >= 0);

ALTER TABLE openair_execution_runs
    ADD CONSTRAINT openair_execution_runs_external_actions_reserved_lte_max
        CHECK (
            max_external_actions IS NULL
            OR external_actions_reserved <= max_external_actions
        );

CREATE TABLE openair_execution_external_action_reservations (
    reservation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES openair_execution_runs(run_id) ON DELETE CASCADE,
    call_id TEXT NOT NULL,
    attempt_kind TEXT NOT NULL,
    max_external_actions INTEGER NULL,
    reserved_after INTEGER NOT NULL CHECK (reserved_after >= 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('reserved', 'rejected')),
    rejection_reason TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT openair_execution_external_action_reservations_attempt_kind
        CHECK (attempt_kind IN ('initial', 'structured_fallback', 'skill_http'))
);

CREATE UNIQUE INDEX uq_openair_external_action_initial_reserved
    ON openair_execution_external_action_reservations(call_id)
    WHERE attempt_kind = 'initial' AND outcome = 'reserved';

CREATE INDEX idx_openair_external_action_reservations_run
    ON openair_execution_external_action_reservations(run_id, created_at);

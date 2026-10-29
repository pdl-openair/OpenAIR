-- Recovery scope closure and reference-only source/successor projection.
-- This projection contains fences and references, never artifact/result copies.

DO $$
DECLARE
    mismatch_count BIGINT;
    mismatch_samples TEXT;
BEGIN
    IF to_regclass('public.openair_task_recovery_requests') IS NULL THEN
        RAISE EXCEPTION
            'p01 recovery projection preflight failed: 20260802_04 request table is required';
    END IF;

    IF to_regclass('public.openair_recovery_execution_projections') IS NOT NULL
       OR to_regprocedure('public.openair_validate_recovery_execution_projection()') IS NOT NULL THEN
        RAISE EXCEPTION
            'p01 recovery projection preflight failed: target projection object already exists; refuse modify';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'openair_execution_runs_task_recovery_scope_fk'
    ) OR EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname IN (
               'uq_openair_recovery_successor_per_source_revision',
               'uq_openair_recovery_successor_run'
           )
    ) THEN
        RAISE EXCEPTION
            'p01 recovery projection preflight failed: target constraint/index already exists; refuse modify';
    END IF;

    SELECT COUNT(*)
      INTO mismatch_count
      FROM openair_execution_runs r
      LEFT JOIN openair_tasks t
        ON t.task_id = r.task_id
       AND t.owner_id = r.owner_id
       AND t.workspace_id = r.workspace_id
     WHERE t.task_id IS NULL;

    IF mismatch_count > 0 THEN
        SELECT string_agg(sample, ', ' ORDER BY sample)
          INTO mismatch_samples
          FROM (
              SELECT left(md5(r.run_id), 12) AS sample
                FROM openair_execution_runs r
                LEFT JOIN openair_tasks t
                  ON t.task_id = r.task_id
                 AND t.owner_id = r.owner_id
                 AND t.workspace_id = r.workspace_id
               WHERE t.task_id IS NULL
               ORDER BY r.run_id
               LIMIT 5
          ) samples;
        RAISE EXCEPTION
            'p01 recovery projection preflight failed: % run(s) have task/scope mismatch; sample run hash(es): %; refuse auto-rewrite',
            mismatch_count,
            COALESCE(mismatch_samples, '');
    END IF;
END
$$;

ALTER TABLE openair_execution_runs
    ADD CONSTRAINT openair_execution_runs_task_recovery_scope_fk
        FOREIGN KEY (task_id, owner_id, workspace_id)
        REFERENCES openair_tasks(task_id, owner_id, workspace_id)
        ON DELETE CASCADE;

CREATE UNIQUE INDEX uq_openair_recovery_successor_per_source_revision
    ON openair_task_recovery_requests(source_run_id, expected_task_revision)
    WHERE successor_run_id IS NOT NULL;

CREATE UNIQUE INDEX uq_openair_recovery_successor_run
    ON openair_task_recovery_requests(successor_run_id)
    WHERE successor_run_id IS NOT NULL;

CREATE TABLE openair_recovery_execution_projections (
    projection_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    successor_run_id TEXT NOT NULL UNIQUE,
    expected_task_revision BIGINT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    recovery_kind TEXT NOT NULL,
    source_lease_epoch_fence BIGINT NOT NULL,
    source_next_event_sequence_fence BIGINT NOT NULL,
    inheritance_mode TEXT NOT NULL DEFAULT 'reference_existing_facts',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT openair_recovery_execution_projections_kind
        CHECK (recovery_kind IN ('retry', 'replan')),
    CONSTRAINT openair_recovery_execution_projections_fences
        CHECK (
            source_lease_epoch_fence >= 0
            AND source_next_event_sequence_fence >= 1
        ),
    CONSTRAINT openair_recovery_execution_projections_reference_only
        CHECK (inheritance_mode = 'reference_existing_facts'),
    CONSTRAINT openair_recovery_execution_projections_distinct_runs
        CHECK (successor_run_id <> source_run_id),
    CONSTRAINT openair_recovery_execution_projections_request_fk
        FOREIGN KEY (
            request_id,
            task_id,
            source_run_id,
            successor_run_id,
            expected_task_revision,
            owner_id,
            workspace_id,
            recovery_kind
        ) REFERENCES openair_task_recovery_requests(
            request_id,
            task_id,
            source_run_id,
            successor_run_id,
            expected_task_revision,
            owner_id,
            workspace_id,
            recovery_kind
        ) ON DELETE RESTRICT,
    CONSTRAINT openair_recovery_execution_projections_source_fk
        FOREIGN KEY (source_run_id, task_id, owner_id, workspace_id)
        REFERENCES openair_execution_runs(run_id, task_id, owner_id, workspace_id)
        ON DELETE RESTRICT,
    CONSTRAINT openair_recovery_execution_projections_successor_fk
        FOREIGN KEY (successor_run_id, task_id, owner_id, workspace_id)
        REFERENCES openair_execution_runs(run_id, task_id, owner_id, workspace_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_openair_recovery_execution_projections_source
    ON openair_recovery_execution_projections(
        source_run_id,
        expected_task_revision,
        created_at
    );

CREATE FUNCTION openair_validate_recovery_execution_projection()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    source_epoch BIGINT;
    source_sequence BIGINT;
    source_number INTEGER;
    successor_number INTEGER;
    request_state TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'recovery execution projection facts are append-only';
    END IF;

    SELECT lease_epoch, next_event_sequence, run_number
      INTO source_epoch, source_sequence, source_number
      FROM openair_execution_runs
     WHERE run_id = NEW.source_run_id
       AND task_id = NEW.task_id
       AND owner_id = NEW.owner_id
       AND workspace_id = NEW.workspace_id;

    SELECT run_number
      INTO successor_number
      FROM openair_execution_runs
     WHERE run_id = NEW.successor_run_id
       AND task_id = NEW.task_id
       AND owner_id = NEW.owner_id
       AND workspace_id = NEW.workspace_id;

    SELECT state
      INTO request_state
      FROM openair_task_recovery_requests
     WHERE request_id = NEW.request_id;

    IF source_epoch IS NULL OR successor_number IS NULL OR request_state IS NULL THEN
        RAISE EXCEPTION 'recovery projection references a missing scoped fact';
    END IF;
    IF request_state <> 'confirmed' THEN
        RAISE EXCEPTION 'recovery projection requires a confirmed request';
    END IF;
    IF NEW.source_lease_epoch_fence <> source_epoch
       OR NEW.source_next_event_sequence_fence <> source_sequence THEN
        RAISE EXCEPTION 'recovery projection source fence does not match the source Run';
    END IF;
    IF successor_number <= source_number THEN
        RAISE EXCEPTION 'recovery successor Run number must follow the source Run';
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER trg_openair_validate_recovery_execution_projection
BEFORE INSERT OR UPDATE OR DELETE ON openair_recovery_execution_projections
FOR EACH ROW
EXECUTE FUNCTION openair_validate_recovery_execution_projection();

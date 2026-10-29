-- P0-1 Recovery authority: request identity, state, source scope, and
-- idempotency facts. Runtime orchestration remains outside this migration.

DO $$
BEGIN
    IF to_regclass('public.openair_tasks') IS NULL
       OR to_regclass('public.openair_execution_runs') IS NULL THEN
        RAISE EXCEPTION
            'p01 task_recovery preflight failed: baseline task/run tables are required';
    END IF;

    IF to_regclass('public.openair_task_recovery_requests') IS NOT NULL THEN
        RAISE EXCEPTION
            'p01 task_recovery preflight failed: target request table already exists; refuse modify';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname IN (
             'uq_openair_tasks_recovery_scope',
             'uq_openair_execution_runs_recovery_scope'
         )
    ) THEN
        RAISE EXCEPTION
            'p01 task_recovery preflight failed: one or more target scope constraints already exist; refuse modify';
    END IF;

    IF to_regprocedure('public.openair_validate_task_recovery_request_insert()') IS NOT NULL
       OR to_regprocedure('public.openair_guard_task_recovery_request_update()') IS NOT NULL THEN
        RAISE EXCEPTION
            'p01 task_recovery preflight failed: target guard function already exists; refuse modify';
    END IF;
END
$$;

ALTER TABLE openair_tasks
    ADD CONSTRAINT uq_openair_tasks_recovery_scope
        UNIQUE (task_id, owner_id, workspace_id);

ALTER TABLE openair_execution_runs
    ADD CONSTRAINT uq_openair_execution_runs_recovery_scope
        UNIQUE (run_id, task_id, owner_id, workspace_id);

CREATE TABLE openair_task_recovery_requests (
    request_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    recovery_kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'requested',
    expected_task_revision BIGINT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    plan_fingerprint TEXT NOT NULL,
    successor_run_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    confirmed_at TIMESTAMPTZ NULL,
    cancelled_at TIMESTAMPTZ NULL,
    CONSTRAINT openair_task_recovery_requests_kind
        CHECK (recovery_kind IN ('retry', 'replan')),
    CONSTRAINT openair_task_recovery_requests_state
        CHECK (state IN ('requested', 'confirmed', 'cancelled')),
    CONSTRAINT openair_task_recovery_requests_revision
        CHECK (expected_task_revision >= 0),
    CONSTRAINT openair_task_recovery_requests_creator_scope
        CHECK (created_by = owner_id),
    CONSTRAINT openair_task_recovery_requests_idempotency_key
        CHECK (length(idempotency_key) BETWEEN 1 AND 256),
    CONSTRAINT openair_task_recovery_requests_payload_fingerprint
        CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT openair_task_recovery_requests_plan_fingerprint
        CHECK (plan_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT openair_task_recovery_requests_distinct_runs
        CHECK (successor_run_id IS NULL OR successor_run_id <> source_run_id),
    CONSTRAINT openair_task_recovery_requests_state_shape
        CHECK (
            (state = 'requested'
                AND successor_run_id IS NULL
                AND confirmed_at IS NULL
                AND cancelled_at IS NULL)
            OR (state = 'confirmed'
                AND successor_run_id IS NOT NULL
                AND confirmed_at IS NOT NULL
                AND cancelled_at IS NULL)
            OR (state = 'cancelled'
                AND successor_run_id IS NULL
                AND confirmed_at IS NULL
                AND cancelled_at IS NOT NULL)
        ),
    CONSTRAINT openair_task_recovery_requests_timestamps
        CHECK (
            updated_at >= created_at
            AND (confirmed_at IS NULL OR confirmed_at >= created_at)
            AND (cancelled_at IS NULL OR cancelled_at >= created_at)
        ),
    CONSTRAINT openair_task_recovery_requests_task_scope_fk
        FOREIGN KEY (task_id, owner_id, workspace_id)
        REFERENCES openair_tasks(task_id, owner_id, workspace_id) ON DELETE RESTRICT,
    CONSTRAINT openair_task_recovery_requests_source_scope_fk
        FOREIGN KEY (source_run_id, task_id, owner_id, workspace_id)
        REFERENCES openair_execution_runs(run_id, task_id, owner_id, workspace_id)
        ON DELETE RESTRICT,
    CONSTRAINT openair_task_recovery_requests_successor_scope_fk
        FOREIGN KEY (successor_run_id, task_id, owner_id, workspace_id)
        REFERENCES openair_execution_runs(run_id, task_id, owner_id, workspace_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_openair_task_recovery_request_idempotency
        UNIQUE (created_by, task_id, idempotency_key),
    CONSTRAINT uq_openair_task_recovery_request_scope
        UNIQUE (request_id, task_id, owner_id, workspace_id),
    CONSTRAINT uq_openair_task_recovery_request_projection
        UNIQUE (
            request_id,
            task_id,
            source_run_id,
            successor_run_id,
            expected_task_revision,
            owner_id,
            workspace_id,
            recovery_kind
        )
);

CREATE INDEX idx_openair_task_recovery_requests_task
    ON openair_task_recovery_requests(
        task_id,
        owner_id,
        workspace_id,
        created_at DESC,
        request_id
    );

CREATE FUNCTION openair_validate_task_recovery_request_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    current_task_revision BIGINT;
    source_state TEXT;
BEGIN
    SELECT revision
      INTO current_task_revision
      FROM openair_tasks
     WHERE task_id = NEW.task_id
       AND owner_id = NEW.owner_id
       AND workspace_id = NEW.workspace_id
     FOR SHARE;

    IF current_task_revision IS NULL THEN
        -- Let the scoped Task FK report the missing/mixed authority row.
        RETURN NEW;
    END IF;
    IF current_task_revision <> NEW.expected_task_revision THEN
        RAISE EXCEPTION
            'recovery request expected Task revision is stale (expected %, current %)',
            NEW.expected_task_revision,
            current_task_revision;
    END IF;

    SELECT state
      INTO source_state
      FROM openair_execution_runs
     WHERE run_id = NEW.source_run_id
       AND task_id = NEW.task_id
       AND owner_id = NEW.owner_id
       AND workspace_id = NEW.workspace_id;

    IF source_state IS NULL THEN
        -- Let the scoped source Run FK report the mismatch.
        RETURN NEW;
    END IF;
    IF source_state NOT IN ('failed', 'blocked', 'abandoned', 'unknown') THEN
        RAISE EXCEPTION
            'recovery request source Run is not in a recoverable failure state: %',
            source_state;
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER trg_openair_validate_task_recovery_request_insert
BEFORE INSERT ON openair_task_recovery_requests
FOR EACH ROW
EXECUTE FUNCTION openair_validate_task_recovery_request_insert();

CREATE FUNCTION openair_guard_task_recovery_request_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'recovery request facts cannot be deleted; cancel requested work instead';
    END IF;

    IF ROW(
        NEW.request_id,
        NEW.task_id,
        NEW.source_run_id,
        NEW.recovery_kind,
        NEW.expected_task_revision,
        NEW.owner_id,
        NEW.workspace_id,
        NEW.created_by,
        NEW.idempotency_key,
        NEW.payload_fingerprint,
        NEW.plan_fingerprint,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.request_id,
        OLD.task_id,
        OLD.source_run_id,
        OLD.recovery_kind,
        OLD.expected_task_revision,
        OLD.owner_id,
        OLD.workspace_id,
        OLD.created_by,
        OLD.idempotency_key,
        OLD.payload_fingerprint,
        OLD.plan_fingerprint,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION
            'recovery request identity, scope, revision, and fingerprints are immutable';
    END IF;

    IF OLD.state <> 'requested'
       AND ROW(NEW.state, NEW.successor_run_id, NEW.confirmed_at, NEW.cancelled_at)
           IS DISTINCT FROM
           ROW(OLD.state, OLD.successor_run_id, OLD.confirmed_at, OLD.cancelled_at) THEN
        RAISE EXCEPTION 'terminal recovery request state is immutable';
    END IF;

    IF OLD.state = 'requested'
       AND NEW.state NOT IN ('requested', 'confirmed', 'cancelled') THEN
        RAISE EXCEPTION 'illegal recovery request state transition';
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER trg_openair_guard_task_recovery_request_update
BEFORE UPDATE OR DELETE ON openair_task_recovery_requests
FOR EACH ROW
EXECUTE FUNCTION openair_guard_task_recovery_request_update();

-- P0-B: persistent, scoped, user-safe task research stream projection.
-- Authoritative scientific facts remain in Task/Run/collaboration tables.

CREATE TABLE IF NOT EXISTS openair_task_stream_events (
    event_sequence BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    source_event_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    source_identity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    presentation_level TEXT NOT NULL CHECK (presentation_level IN ('primary', 'detail')),
    work_unit_id TEXT NULL,
    headline TEXT NOT NULL,
    summary TEXT NULL,
    invalidate_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    UNIQUE(owner_id, workspace_id, task_id, source_identity, source_event_id),
    CHECK (length(btrim(event_id)) > 0),
    CHECK (length(btrim(source_event_id)) > 0),
    CHECK (length(btrim(source_identity)) > 0),
    CHECK (length(btrim(headline)) > 0),
    CHECK (jsonb_typeof(invalidate_scopes) = 'array'),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_openair_task_stream_scope_sequence
    ON openair_task_stream_events(owner_id, workspace_id, task_id, source_identity, event_sequence);
CREATE INDEX IF NOT EXISTS idx_openair_task_stream_expiry
    ON openair_task_stream_events(expires_at, event_sequence);

CREATE OR REPLACE FUNCTION openair_validate_task_stream_scope()
RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM openair_tasks
        WHERE task_id = NEW.task_id
          AND owner_id = NEW.owner_id
          AND workspace_id = NEW.workspace_id
    ) THEN
        RAISE EXCEPTION 'task stream scope mismatch for task %', NEW.task_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_openair_validate_task_stream_scope ON openair_task_stream_events;
CREATE TRIGGER trg_openair_validate_task_stream_scope
BEFORE INSERT ON openair_task_stream_events
FOR EACH ROW EXECUTE FUNCTION openair_validate_task_stream_scope();

CREATE OR REPLACE FUNCTION openair_notify_task_stream_event()
RETURNS TRIGGER AS $$
BEGIN
    -- Notification contains only routing metadata. Event body stays in PostgreSQL.
    PERFORM pg_notify(
        'openair_task_stream_events',
        json_build_object(
            'taskId', NEW.task_id,
            'ownerId', NEW.owner_id,
            'workspaceId', NEW.workspace_id,
            'sequence', NEW.event_sequence
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_openair_notify_task_stream_event ON openair_task_stream_events;
CREATE TRIGGER trg_openair_notify_task_stream_event
AFTER INSERT ON openair_task_stream_events
FOR EACH ROW EXECUTE FUNCTION openair_notify_task_stream_event();

CREATE OR REPLACE FUNCTION openair_notify_task_authoritative_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Wake listeners after the transaction commits. They re-read authoritative facts.
    PERFORM pg_notify(
        'openair_task_stream_events',
        json_build_object(
            'taskId', NEW.task_id,
            'ownerId', NEW.owner_id,
            'workspaceId', NEW.workspace_id,
            'authoritativeChange', true
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_openair_notify_task_authoritative_change ON openair_tasks;
CREATE TRIGGER trg_openair_notify_task_authoritative_change
AFTER INSERT OR UPDATE ON openair_tasks
FOR EACH ROW EXECUTE FUNCTION openair_notify_task_authoritative_change();


CREATE OR REPLACE FUNCTION openair_notify_execution_run_event_change()
RETURNS TRIGGER AS $$
DECLARE
    run_scope RECORD;
BEGIN
    SELECT task_id, owner_id, workspace_id
      INTO run_scope
      FROM openair_execution_runs
     WHERE run_id = NEW.run_id;
    IF FOUND THEN
        PERFORM pg_notify(
            'openair_task_stream_events',
            json_build_object(
                'taskId', run_scope.task_id,
                'ownerId', run_scope.owner_id,
                'workspaceId', run_scope.workspace_id,
                'authoritativeRunEvent', true
            )::text
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_openair_notify_execution_run_event_change ON openair_execution_run_events;
CREATE TRIGGER trg_openair_notify_execution_run_event_change
AFTER INSERT ON openair_execution_run_events
FOR EACH ROW EXECUTE FUNCTION openair_notify_execution_run_event_change();

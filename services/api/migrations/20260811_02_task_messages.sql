-- E1 task messages: dedicated append-only table for the agent chat transcript.
-- Replaces the unbounded payload->agentMessages[] JSONB array (read-whole /
-- write-whole under row lock, no pagination, no cursor) with O(1) INSERTs and
-- cursor pagination. Postgres mode only; JSON store keeps its in-memory array.
--
-- Per-task monotonic `seq` is the cursor anchor + ordering; `message_uid` is the
-- logical message id (msg_xxx chat / msg_dispatch_xxx / msg_controller_command_xxx)
-- and the idempotency key (UNIQUE per task → ON CONFLICT DO NOTHING replay-safe).

CREATE TABLE IF NOT EXISTS openair_task_messages (
    message_id         BIGSERIAL PRIMARY KEY,
    task_id            TEXT NOT NULL REFERENCES openair_tasks(task_id) ON DELETE CASCADE,
    owner_id           TEXT NOT NULL,
    workspace_id       TEXT NOT NULL,
    seq                BIGINT NOT NULL,
    message_uid        TEXT NOT NULL,
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content_text       TEXT NOT NULL,
    runtime            TEXT,
    message_type       TEXT NOT NULL DEFAULT 'chat',
    capability_call_id TEXT,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_id, seq),
    UNIQUE (task_id, message_uid)
);

-- Core cursor pagination index (newest-first windowed scan by seq).
CREATE INDEX IF NOT EXISTS idx_task_messages_cursor
    ON openair_task_messages (task_id, seq DESC);
-- Multi-tenant scope filter.
CREATE INDEX IF NOT EXISTS idx_task_messages_scope
    ON openair_task_messages (owner_id, workspace_id, task_id);

-- Scope guard (mirrors openair_validate_task_stream_scope on openair_task_stream_events):
-- a message row's owner/workspace must match its task row, blocking cross-tenant writes.
CREATE OR REPLACE FUNCTION openair_validate_task_message_scope() RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM openair_tasks
         WHERE task_id = NEW.task_id
           AND owner_id = NEW.owner_id
           AND workspace_id = NEW.workspace_id
    ) THEN
        RAISE EXCEPTION 'task_message_scope_mismatch';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_openair_validate_task_message_scope ON openair_task_messages;
CREATE TRIGGER trg_openair_validate_task_message_scope
    BEFORE INSERT ON openair_task_messages
    FOR EACH ROW EXECUTE FUNCTION openair_validate_task_message_scope();

-- Recovery preflight messages are append-only scoped inputs. Confirmation is
-- rejected unless at least one message is linked to the request first.

DO $$
BEGIN
    IF to_regclass('public.openair_task_recovery_requests') IS NULL THEN
        RAISE EXCEPTION
            'p01 recovery message preflight failed: 20260802_04 request table is required';
    END IF;

    IF to_regclass('public.openair_recovery_messages') IS NOT NULL
       OR to_regclass('public.openair_recovery_request_messages') IS NOT NULL THEN
        RAISE EXCEPTION
            'p01 recovery message preflight failed: target message table already exists; refuse modify';
    END IF;

    IF to_regprocedure('public.openair_enforce_recovery_message_preflight()') IS NOT NULL
       OR to_regprocedure('public.openair_guard_recovery_message_fact()') IS NOT NULL THEN
        RAISE EXCEPTION
            'p01 recovery message preflight failed: target trigger function already exists; refuse modify';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM openair_task_recovery_requests
         WHERE state = 'confirmed'
    ) THEN
        RAISE EXCEPTION
            'p01 recovery message preflight failed: confirmed request(s) predate message authority; refuse invent preflight inputs';
    END IF;
END
$$;

CREATE TABLE openair_recovery_messages (
    message_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    message_kind TEXT NOT NULL,
    content TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT openair_recovery_messages_creator_scope
        CHECK (created_by = owner_id),
    CONSTRAINT openair_recovery_messages_kind
        CHECK (message_kind IN ('recovery_instruction', 'failure_context')),
    CONSTRAINT openair_recovery_messages_content
        CHECK (length(btrim(content)) > 0),
    CONSTRAINT openair_recovery_messages_payload_fingerprint
        CHECK (payload_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT openair_recovery_messages_task_scope_fk
        FOREIGN KEY (task_id, owner_id, workspace_id)
        REFERENCES openair_tasks(task_id, owner_id, workspace_id) ON DELETE RESTRICT,
    CONSTRAINT uq_openair_recovery_message_scope
        UNIQUE (message_id, task_id, owner_id, workspace_id)
);

CREATE INDEX idx_openair_recovery_messages_task
    ON openair_recovery_messages(
        task_id,
        owner_id,
        workspace_id,
        created_at,
        message_id
    );

CREATE TABLE openair_recovery_request_messages (
    request_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (request_id, message_id),
    CONSTRAINT openair_recovery_request_messages_ordinal
        CHECK (ordinal > 0),
    CONSTRAINT uq_openair_recovery_request_message_ordinal
        UNIQUE (request_id, ordinal),
    CONSTRAINT openair_recovery_request_messages_request_scope_fk
        FOREIGN KEY (request_id, task_id, owner_id, workspace_id)
        REFERENCES openair_task_recovery_requests(
            request_id,
            task_id,
            owner_id,
            workspace_id
        ) ON DELETE RESTRICT,
    CONSTRAINT openair_recovery_request_messages_message_scope_fk
        FOREIGN KEY (message_id, task_id, owner_id, workspace_id)
        REFERENCES openair_recovery_messages(
            message_id,
            task_id,
            owner_id,
            workspace_id
        ) ON DELETE RESTRICT
);

CREATE INDEX idx_openair_recovery_request_messages_message
    ON openair_recovery_request_messages(message_id, request_id);

CREATE FUNCTION openair_enforce_recovery_message_preflight()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.state = 'confirmed' THEN
        IF TG_OP = 'INSERT' THEN
            RAISE EXCEPTION
                'recovery confirmation requires a same-Task, same-scope preflight message';
        END IF;
        IF OLD.state <> 'confirmed'
           AND NOT EXISTS (
               SELECT 1
                 FROM openair_recovery_request_messages rm
                WHERE rm.request_id = NEW.request_id
                  AND rm.task_id = NEW.task_id
                  AND rm.owner_id = NEW.owner_id
                  AND rm.workspace_id = NEW.workspace_id
           ) THEN
            RAISE EXCEPTION
                'recovery confirmation requires a same-Task, same-scope preflight message';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_openair_enforce_recovery_message_preflight
BEFORE INSERT OR UPDATE OF state ON openair_task_recovery_requests
FOR EACH ROW
EXECUTE FUNCTION openair_enforce_recovery_message_preflight();

CREATE FUNCTION openair_guard_recovery_message_fact()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'recovery message and request-message facts are append-only';
END
$$;

CREATE TRIGGER trg_openair_guard_recovery_message_fact
BEFORE UPDATE OR DELETE ON openair_recovery_messages
FOR EACH ROW
EXECUTE FUNCTION openair_guard_recovery_message_fact();

CREATE TRIGGER trg_openair_guard_recovery_request_message_fact
BEFORE UPDATE OR DELETE ON openair_recovery_request_messages
FOR EACH ROW
EXECUTE FUNCTION openair_guard_recovery_message_fact();

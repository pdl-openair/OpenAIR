-- E1 down: drop the task messages table + scope trigger/function.

DROP TRIGGER IF EXISTS trg_openair_validate_task_message_scope ON openair_task_messages;
DROP FUNCTION IF EXISTS openair_validate_task_message_scope();
DROP INDEX IF EXISTS idx_task_messages_scope;
DROP INDEX IF EXISTS idx_task_messages_cursor;
DROP TABLE IF EXISTS openair_task_messages;

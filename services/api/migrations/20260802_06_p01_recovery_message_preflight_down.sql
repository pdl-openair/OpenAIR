-- Manual rollback for 20260802_06_p01_recovery_message_preflight.sql.

DROP TRIGGER IF EXISTS trg_openair_enforce_recovery_message_preflight
    ON openair_task_recovery_requests;
DROP FUNCTION IF EXISTS openair_enforce_recovery_message_preflight();

DROP TRIGGER IF EXISTS trg_openair_guard_recovery_request_message_fact
    ON openair_recovery_request_messages;
DROP TRIGGER IF EXISTS trg_openair_guard_recovery_message_fact
    ON openair_recovery_messages;
DROP FUNCTION IF EXISTS openair_guard_recovery_message_fact();

DROP INDEX IF EXISTS idx_openair_recovery_request_messages_message;
DROP TABLE IF EXISTS openair_recovery_request_messages;
DROP INDEX IF EXISTS idx_openair_recovery_messages_task;
DROP TABLE IF EXISTS openair_recovery_messages;

DELETE FROM openair_schema_migrations
 WHERE version = '20260802_06_p01_recovery_message_preflight.sql';

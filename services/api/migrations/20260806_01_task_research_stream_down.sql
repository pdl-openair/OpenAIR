DROP TRIGGER IF EXISTS trg_openair_notify_execution_run_event_change ON openair_execution_run_events;
DROP FUNCTION IF EXISTS openair_notify_execution_run_event_change();
DROP TRIGGER IF EXISTS trg_openair_notify_task_authoritative_change ON openair_tasks;
DROP FUNCTION IF EXISTS openair_notify_task_authoritative_change();
DROP TRIGGER IF EXISTS trg_openair_notify_task_stream_event ON openair_task_stream_events;
DROP FUNCTION IF EXISTS openair_notify_task_stream_event();
DROP TRIGGER IF EXISTS trg_openair_validate_task_stream_scope ON openair_task_stream_events;
DROP FUNCTION IF EXISTS openair_validate_task_stream_scope();
DROP TABLE IF EXISTS openair_task_stream_events;

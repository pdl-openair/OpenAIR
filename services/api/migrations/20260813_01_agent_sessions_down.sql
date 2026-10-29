-- Manual rollback for 20260813_01_agent_sessions.sql only.
DROP INDEX IF EXISTS idx_openair_agent_sessions_scope_updated;
DROP TABLE IF EXISTS openair_agent_sessions;
DELETE FROM openair_schema_migrations
 WHERE version = '20260813_01_agent_sessions.sql';

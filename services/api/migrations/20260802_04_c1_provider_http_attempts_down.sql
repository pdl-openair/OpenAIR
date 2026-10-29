-- Manual rollback for 20260802_04_c1_provider_http_attempts.sql only.
DROP INDEX IF EXISTS idx_openair_provider_http_attempts_started;
DROP TABLE IF EXISTS openair_provider_http_attempts;
DELETE FROM openair_schema_migrations
 WHERE version = '20260802_04_c1_provider_http_attempts.sql';

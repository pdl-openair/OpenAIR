-- Manual rollback for 20260804_01_resource_saves.sql only.
DROP INDEX IF EXISTS idx_openair_resource_saves_scope_created;
DROP TABLE IF EXISTS openair_resource_saves;
DELETE FROM openair_schema_migrations
 WHERE version = '20260804_01_resource_saves.sql';

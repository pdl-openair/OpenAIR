DROP TRIGGER IF EXISTS trg_openair_enforce_research_package_asset ON openair_artifacts;
DROP FUNCTION IF EXISTS openair_enforce_research_package_asset();
DROP INDEX IF EXISTS uq_openair_research_package_identity;
DELETE FROM openair_schema_migrations
 WHERE version = '20260917_01_research_package_asset_immutability.sql';

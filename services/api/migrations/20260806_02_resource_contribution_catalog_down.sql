DROP INDEX IF EXISTS idx_contributions_catalog_resource;
ALTER TABLE openair_contributions
    DROP CONSTRAINT IF EXISTS openair_contributions_system_check_status_check,
    DROP CONSTRAINT IF EXISTS openair_contributions_domain_check,
    DROP CONSTRAINT IF EXISTS openair_contributions_discovery_scene_check,
    DROP COLUMN IF EXISTS published_manifest,
    DROP COLUMN IF EXISTS published_at,
    DROP COLUMN IF EXISTS release_version,
    DROP COLUMN IF EXISTS catalog_resource_id,
    DROP COLUMN IF EXISTS system_check_issues,
    DROP COLUMN IF EXISTS system_check_status,
    DROP COLUMN IF EXISTS discovery_scene,
    DROP COLUMN IF EXISTS domain;
DELETE FROM openair_schema_migrations
 WHERE version = '20260806_02_resource_contribution_catalog.sql';

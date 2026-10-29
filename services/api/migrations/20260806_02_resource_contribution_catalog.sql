-- Resource Plaza B line: system-check facts and immutable public catalog admission.

ALTER TABLE openair_contributions
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'general',
    ADD COLUMN IF NOT EXISTS discovery_scene TEXT NOT NULL DEFAULT 'research_materials',
    ADD COLUMN IF NOT EXISTS system_check_status TEXT NOT NULL DEFAULT 'not_checked',
    ADD COLUMN IF NOT EXISTS system_check_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS catalog_resource_id TEXT,
    ADD COLUMN IF NOT EXISTS release_version TEXT,
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS published_manifest JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS idx_contributions_catalog_resource
    ON openair_contributions (catalog_resource_id)
    WHERE catalog_resource_id IS NOT NULL;

ALTER TABLE openair_contributions
    DROP CONSTRAINT IF EXISTS openair_contributions_system_check_status_check;
ALTER TABLE openair_contributions
    ADD CONSTRAINT openair_contributions_system_check_status_check
    CHECK (system_check_status IN ('not_checked', 'passed', 'needs_attention'));

ALTER TABLE openair_contributions
    DROP CONSTRAINT IF EXISTS openair_contributions_domain_check;
ALTER TABLE openair_contributions
    ADD CONSTRAINT openair_contributions_domain_check
    CHECK (domain IN ('cs_research', 'materials', 'general'));

ALTER TABLE openair_contributions
    DROP CONSTRAINT IF EXISTS openair_contributions_discovery_scene_check;
ALTER TABLE openair_contributions
    ADD CONSTRAINT openair_contributions_discovery_scene_check
    CHECK (discovery_scene IN ('research_materials', 'data_results', 'experiment_computation', 'research_quality'));

ALTER TABLE research_start_sessions
    ADD COLUMN catalog_intent_id TEXT NULL,
    ADD COLUMN selected_resource_id TEXT NULL,
    ADD COLUMN selected_resource_version TEXT NULL,
    ADD COLUMN selected_capability_id TEXT NULL;

ALTER TABLE research_start_sessions
    ADD CONSTRAINT chk_research_start_sessions_catalog_snapshot_all_or_none
    CHECK (
        (catalog_intent_id IS NULL
         AND selected_resource_id IS NULL
         AND selected_resource_version IS NULL
         AND selected_capability_id IS NULL)
        OR
        (catalog_intent_id IS NOT NULL
         AND selected_resource_id IS NOT NULL
         AND selected_resource_version IS NOT NULL
         AND selected_capability_id IS NOT NULL)
    );

ALTER TABLE research_start_sessions
    ADD CONSTRAINT chk_research_start_sessions_catalog_intent_id_format
    CHECK (catalog_intent_id IS NULL OR catalog_intent_id ~ '^intent_[0-9a-f]{12}$');

CREATE UNIQUE INDEX uq_research_start_sessions_catalog_intent_scope
    ON research_start_sessions(owner_id, workspace_id, catalog_intent_id)
    WHERE catalog_intent_id IS NOT NULL;

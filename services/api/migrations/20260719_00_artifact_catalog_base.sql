-- Base Artifact catalog required by all later ordered PostgreSQL migrations.
-- This migration replaces production DDL that previously lived in ArtifactRepository.bootstrap().
CREATE TABLE IF NOT EXISTS openair_artifacts (
    artifact_id               TEXT PRIMARY KEY,
    owner_id                  TEXT NOT NULL,
    workspace_id              TEXT NOT NULL,
    created_by                TEXT NULL,
    kind                      TEXT NOT NULL,
    origin                    TEXT NULL,
    title                     TEXT NOT NULL,
    summary                   TEXT NULL,
    format                    TEXT NULL,
    mime_type                 TEXT NULL,
    storage_type              TEXT NULL,
    storage_scope             TEXT NULL,
    storage_ref               TEXT NULL,
    external_provider         TEXT NULL,
    external_uri              TEXT NULL,
    content_size              BIGINT NULL,
    content_sha256            TEXT NULL,
    source_task_id            TEXT NULL,
    source_task_title         TEXT NULL,
    source_capability_call_id TEXT NULL,
    related_stage             TEXT NULL,
    related_skill_id          TEXT NULL,
    metadata                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NULL,
    updated_at                TIMESTAMPTZ NULL,
    archived_at               TIMESTAMPTZ NULL
);
ALTER TABLE openair_artifacts
    ADD COLUMN IF NOT EXISTS storage_scope TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_scope
    ON openair_artifacts(owner_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_task
    ON openair_artifacts(source_task_id);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_kind
    ON openair_artifacts(kind);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_origin
    ON openair_artifacts(origin);

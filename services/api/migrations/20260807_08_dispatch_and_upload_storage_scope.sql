-- Line C main-flow scope propagation.
-- Persist external physical-object scope before bytes can outlive the current process.

ALTER TABLE openair_dispatch
    ADD COLUMN IF NOT EXISTS result_storage_scope TEXT NULL;

-- Existing local result blobs have a task-scoped physical identity. Legacy external
-- rows cannot prove their historical bucket and intentionally remain scope=NULL.
UPDATE openair_dispatch
   SET result_storage_scope = task_id
 WHERE result_storage_type = 'local'
   AND result_storage_ref IS NOT NULL
   AND btrim(COALESCE(result_storage_scope, '')) = '';

ALTER TABLE openair_dispatch
    DROP CONSTRAINT IF EXISTS openair_dispatch_result_blob_complete_check;
ALTER TABLE openair_dispatch
    ADD CONSTRAINT openair_dispatch_result_blob_complete_check CHECK (
        (result_storage_type IS NULL
         AND result_storage_scope IS NULL
         AND result_storage_ref IS NULL
         AND result_content_sha256 IS NULL)
        OR
        (result_storage_type IN ('local', 'external')
         AND length(btrim(result_storage_ref)) > 0
         AND (
             (result_storage_type = 'local' AND result_storage_scope = task_id)
             OR
             (result_storage_type = 'external' AND (
                 result_storage_scope IS NULL OR length(btrim(result_storage_scope)) > 0
             ))
         )
         AND (result_content_sha256 IS NULL OR result_content_sha256 ~ '^[0-9a-f]{64}$'))
    );

ALTER TABLE openair_artifact_uploads
    ADD COLUMN IF NOT EXISTS storage_scope TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_openair_dispatch_result_storage_identity
    ON openair_dispatch(result_storage_type, result_storage_scope, result_storage_ref)
    WHERE result_storage_ref IS NOT NULL;

-- Historical versions are append-only科研 facts. Updating an Artifact may add a new
-- versionId, but it may not remove an already-published version except through the
-- explicit Artifact-delete transaction (which deletes the whole row, not UPDATE metadata).
CREATE OR REPLACE FUNCTION openair_enforce_historical_version_immutability()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_version JSONB;
    new_version JSONB;
    version_id_value TEXT;
BEGIN
    FOR old_version IN
        SELECT value FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(OLD.metadata->'versions') = 'array'
                 THEN OLD.metadata->'versions' ELSE '[]'::jsonb END
        ) AS rows(value)
    LOOP
        version_id_value := btrim(COALESCE(old_version->>'versionId', ''));
        IF version_id_value = '' THEN
            CONTINUE;
        END IF;
        SELECT value INTO new_version
          FROM jsonb_array_elements(
              CASE WHEN jsonb_typeof(NEW.metadata->'versions') = 'array'
                   THEN NEW.metadata->'versions' ELSE '[]'::jsonb END
          ) AS rows(value)
         WHERE btrim(COALESCE(value->>'versionId', '')) = version_id_value
         LIMIT 1;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'artifact_historical_version_immutable'
                USING ERRCODE = '55000';
        END IF;
        IF OLD.source_task_id IS DISTINCT FROM NEW.source_task_id
           OR old_version->>'storageType' IS DISTINCT FROM new_version->>'storageType'
           OR old_version->>'storageScope' IS DISTINCT FROM new_version->>'storageScope'
           OR old_version->>'externalKey' IS DISTINCT FROM new_version->>'externalKey'
           OR old_version->>'path' IS DISTINCT FROM new_version->>'path'
           OR old_version->>'inlineContent' IS DISTINCT FROM new_version->>'inlineContent'
           OR old_version->>'contentSha256' IS DISTINCT FROM new_version->>'contentSha256'
           OR old_version->'contentSize' IS DISTINCT FROM new_version->'contentSize'
        THEN
            RAISE EXCEPTION 'artifact_historical_version_immutable'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

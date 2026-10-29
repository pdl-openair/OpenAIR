DROP INDEX IF EXISTS idx_openair_dispatch_result_storage_identity;
ALTER TABLE openair_artifact_uploads DROP COLUMN IF EXISTS storage_scope;
ALTER TABLE openair_dispatch DROP CONSTRAINT IF EXISTS openair_dispatch_result_blob_complete_check;
ALTER TABLE openair_dispatch
    ADD CONSTRAINT openair_dispatch_result_blob_complete_check CHECK (
        (result_storage_type IS NULL AND result_storage_ref IS NULL AND result_content_sha256 IS NULL)
        OR
        (result_storage_type IN ('local', 'external')
         AND length(btrim(result_storage_ref)) > 0
         AND result_content_sha256 ~ '^[0-9a-f]{64}$')
    );
ALTER TABLE openair_dispatch DROP COLUMN IF EXISTS result_storage_scope;
-- Historical-version immutability is intentionally not weakened on downgrade.

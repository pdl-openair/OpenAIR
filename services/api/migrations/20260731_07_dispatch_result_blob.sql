ALTER TABLE openair_dispatch
    ADD COLUMN result_storage_type TEXT NULL,
    ADD COLUMN result_storage_ref TEXT NULL,
    ADD COLUMN result_content_sha256 CHAR(64) NULL;

ALTER TABLE openair_dispatch
    ADD CONSTRAINT openair_dispatch_result_blob_complete_check CHECK (
        (result_storage_type IS NULL AND result_storage_ref IS NULL AND result_content_sha256 IS NULL)
        OR
        (result_storage_type IN ('local', 'external')
         AND length(btrim(result_storage_ref)) > 0
         AND result_content_sha256 ~ '^[0-9a-f]{64}$')
    );

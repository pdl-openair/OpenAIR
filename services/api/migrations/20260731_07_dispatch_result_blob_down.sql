LOCK TABLE openair_dispatch IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM openair_dispatch
         WHERE result_storage_ref IS NOT NULL
         LIMIT 1
    ) THEN
        RAISE EXCEPTION 'refusing to drop non-empty dispatch result blob evidence';
    END IF;
END
$$;

ALTER TABLE openair_dispatch
    DROP CONSTRAINT openair_dispatch_result_blob_complete_check,
    DROP COLUMN result_content_sha256,
    DROP COLUMN result_storage_ref,
    DROP COLUMN result_storage_type;

DELETE FROM openair_schema_migrations
 WHERE version = '20260731_07_dispatch_result_blob.sql';

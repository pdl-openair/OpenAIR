
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM openair_artifact_storage_references
         WHERE reference_kind = 'historical'
    ) THEN
        RAISE EXCEPTION 'cannot_drop_artifact_storage_references_with_historical_ownership';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_retire_artifact_storage_references ON openair_artifacts;
DROP TRIGGER IF EXISTS trg_openair_sync_artifact_storage_references ON openair_artifacts;
DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_reference
    ON openair_artifact_storage_references;
DROP FUNCTION IF EXISTS openair_retire_artifact_storage_references();
DROP FUNCTION IF EXISTS openair_sync_artifact_storage_references();
DROP FUNCTION IF EXISTS openair_fence_artifact_storage_reference();
DROP TABLE IF EXISTS openair_artifact_storage_references;

-- Restore the previous current-artifact-only trigger for a safe downgrade with no
-- historical ownership rows.
DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_registration ON openair_artifacts;
CREATE TRIGGER trg_openair_fence_artifact_storage_registration
BEFORE INSERT OR UPDATE OF storage_type, storage_ref, source_task_id ON openair_artifacts
FOR EACH ROW EXECUTE FUNCTION openair_fence_artifact_storage_registration();

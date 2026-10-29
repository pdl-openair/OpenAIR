DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_registration
    ON openair_artifacts;
DROP FUNCTION IF EXISTS openair_fence_artifact_storage_registration();
DROP FUNCTION IF EXISTS openair_artifact_storage_lock_key(TEXT, TEXT, TEXT);

-- Deliberately retain storage_scope and the scoped unique index. Collapsing local
-- identities from (task_id, path) back to path-only can destroy valid rows during
-- rollback. Removing the correctness trigger is reversible; lossy identity
-- collapse is not.

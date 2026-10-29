DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM openair_artifact_storage_references
         WHERE reference_kind = 'historical'
            OR (storage_type = 'external' AND btrim(COALESCE(storage_scope, '')) <> '')
    ) OR EXISTS (
        SELECT 1 FROM openair_artifacts
         WHERE storage_type = 'external'
           AND btrim(COALESCE(storage_scope, '')) <> ''
    ) THEN
        RAISE EXCEPTION 'cannot_drop_final_artifact_storage_identity_with_durable_ownership';
    END IF;
END;
$$;
DROP TRIGGER IF EXISTS trg_openair_enforce_historical_storage_reference_immutability
    ON openair_artifact_storage_references;
DROP FUNCTION IF EXISTS openair_enforce_historical_storage_reference_immutability();
DROP TRIGGER IF EXISTS trg_openair_enforce_historical_version_immutability ON openair_artifacts;
DROP FUNCTION IF EXISTS openair_enforce_historical_version_immutability();
DROP TRIGGER IF EXISTS trg_openair_assign_artifact_storage_scope ON openair_artifacts;
DROP FUNCTION IF EXISTS openair_assign_artifact_storage_scope();
DROP TABLE IF EXISTS openair_artifact_storage_runtime;
ALTER TABLE openair_artifacts DROP COLUMN IF EXISTS storage_scope;

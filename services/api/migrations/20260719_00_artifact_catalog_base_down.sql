-- Artifact catalog predates this migration in deployed environments; never drop it automatically.
DO $$
BEGIN
    RAISE EXCEPTION 'cannot_drop_artifact_catalog_base';
END;
$$;

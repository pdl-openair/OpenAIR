CREATE TABLE openair_registry_snapshots (
    content_hash TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT openair_registry_snapshots_content_hash_format
        CHECK (content_hash ~ '^sha256:[0-9a-f]{64}$')
);

CREATE FUNCTION openair_registry_snapshots_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'openair_registry_snapshots is append-only'
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER openair_registry_snapshots_reject_update_delete
BEFORE UPDATE OR DELETE ON openair_registry_snapshots
FOR EACH STATEMENT
EXECUTE FUNCTION openair_registry_snapshots_reject_mutation();

CREATE TRIGGER openair_registry_snapshots_reject_truncate
BEFORE TRUNCATE ON openair_registry_snapshots
FOR EACH STATEMENT
EXECUTE FUNCTION openair_registry_snapshots_reject_mutation();

REVOKE UPDATE, DELETE, TRUNCATE ON openair_registry_snapshots FROM PUBLIC;

COMMENT ON TABLE openair_registry_snapshots IS
    'Content-addressed immutable RegistrySnapshot archive; database-enforced append-only storage.';

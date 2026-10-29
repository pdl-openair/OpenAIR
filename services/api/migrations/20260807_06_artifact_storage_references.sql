
CREATE TABLE IF NOT EXISTS openair_artifact_storage_references (
    artifact_id      TEXT NOT NULL REFERENCES openair_artifacts(artifact_id) ON DELETE CASCADE,
    reference_kind   TEXT NOT NULL CHECK (reference_kind IN ('current', 'historical')),
    version_id       TEXT NOT NULL DEFAULT '',
    source_task_id   TEXT NOT NULL,
    storage_type     TEXT NOT NULL CHECK (storage_type IN ('inline', 'external', 'local')),
    storage_scope    TEXT NOT NULL DEFAULT '',
    storage_ref      TEXT NOT NULL,
    content_sha256   TEXT NULL,
    content_size     BIGINT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (artifact_id, reference_kind, version_id),
    CHECK (
        (reference_kind = 'current' AND version_id = '')
        OR (reference_kind = 'historical' AND version_id <> '')
    )
);

CREATE INDEX IF NOT EXISTS idx_openair_artifact_storage_refs_identity
    ON openair_artifact_storage_references(storage_type, storage_scope, storage_ref);
CREATE INDEX IF NOT EXISTS idx_openair_artifact_storage_refs_artifact
    ON openair_artifact_storage_references(artifact_id, reference_kind, version_id);

-- Backfill current references. External/local references remain ownership facts even
-- when legacy digest metadata is incomplete; content routes still fail closed.
INSERT INTO openair_artifact_storage_references (
    artifact_id, reference_kind, version_id, source_task_id,
    storage_type, storage_scope, storage_ref, content_sha256, content_size
)
SELECT artifact_id, 'current', '', source_task_id,
       storage_type,
       CASE
           WHEN storage_type = 'local' THEN source_task_id
           WHEN storage_type = 'inline' THEN artifact_id
           ELSE btrim(COALESCE(storage_scope, ''))
       END,
       CASE WHEN storage_type = 'inline' THEN content_sha256 ELSE storage_ref END,
       content_sha256, content_size
  FROM openair_artifacts
 WHERE source_task_id IS NOT NULL
   AND btrim(source_task_id) <> ''
   AND (
       (storage_type = 'external' AND storage_ref IS NOT NULL AND btrim(storage_ref) <> ''
        AND btrim(COALESCE(storage_scope, '')) <> '')
       OR (storage_type = 'local' AND storage_ref IS NOT NULL AND btrim(storage_ref) <> '')
       OR (
           storage_type = 'inline'
           AND content_sha256 ~ '^[0-9A-Fa-f]{64}$'
           AND content_size IS NOT NULL AND content_size >= 0
       )
   )
ON CONFLICT (artifact_id, reference_kind, version_id) DO NOTHING;

-- Backfill only immutable/readable historical versions. Incomplete legacy version
-- metadata remains visible as metadata but unavailable for content reads.
INSERT INTO openair_artifact_storage_references (
    artifact_id, reference_kind, version_id, source_task_id,
    storage_type, storage_scope, storage_ref, content_sha256, content_size
)
SELECT artifact.artifact_id,
       'historical',
       btrim(version.value->>'versionId'),
       artifact.source_task_id,
       lower(btrim(version.value->>'storageType')),
       CASE
           WHEN lower(btrim(version.value->>'storageType')) = 'local' THEN artifact.source_task_id
           WHEN lower(btrim(version.value->>'storageType')) = 'inline' THEN artifact.artifact_id
           ELSE btrim(COALESCE(version.value->>'storageScope', ''))
       END,
       CASE
           WHEN lower(btrim(version.value->>'storageType')) = 'external'
               THEN btrim(version.value->>'externalKey')
           WHEN lower(btrim(version.value->>'storageType')) = 'local'
               THEN btrim(version.value->>'path')
           ELSE lower(version.value->>'contentSha256')
       END,
       lower(version.value->>'contentSha256'),
       (version.value->>'contentSize')::BIGINT
  FROM openair_artifacts AS artifact
 CROSS JOIN LATERAL jsonb_array_elements(
     CASE WHEN jsonb_typeof(artifact.metadata->'versions') = 'array'
          THEN artifact.metadata->'versions' ELSE '[]'::jsonb END
 ) AS version(value)
 WHERE artifact.source_task_id IS NOT NULL
   AND btrim(artifact.source_task_id) <> ''
   AND btrim(COALESCE(version.value->>'versionId', '')) <> ''
   AND lower(btrim(COALESCE(version.value->>'storageType', ''))) IN ('inline', 'external', 'local')
   AND COALESCE(version.value->>'contentSha256', '') ~ '^[0-9A-Fa-f]{64}$'
   AND jsonb_typeof(version.value->'contentSize') = 'number'
   AND (version.value->>'contentSize') ~ '^[0-9]+$'
   AND (
       (lower(btrim(version.value->>'storageType')) = 'external'
        AND btrim(COALESCE(version.value->>'externalKey', '')) <> ''
        AND btrim(COALESCE(version.value->>'storageScope', '')) <> '')
       OR (lower(btrim(version.value->>'storageType')) = 'local'
        AND btrim(COALESCE(version.value->>'path', '')) <> '')
       OR lower(btrim(version.value->>'storageType')) = 'inline'
   )
ON CONFLICT (artifact_id, reference_kind, version_id) DO NOTHING;

CREATE OR REPLACE FUNCTION openair_fence_artifact_storage_reference()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    cleanup_row RECORD;
BEGIN
    IF TG_OP = 'UPDATE' AND (
        OLD.storage_type IS DISTINCT FROM NEW.storage_type
        OR OLD.storage_scope IS DISTINCT FROM NEW.storage_scope
        OR OLD.storage_ref IS DISTINCT FROM NEW.storage_ref
    ) AND OLD.storage_type IN ('external', 'local') THEN
        PERFORM pg_advisory_xact_lock(
            openair_artifact_storage_lock_key(
                OLD.storage_type, OLD.storage_scope, OLD.storage_ref
            )
        );
        INSERT INTO openair_artifact_cleanup_outbox (
            cleanup_id, task_id, artifact_id, storage_type, storage_scope,
            storage_ref, reason_code, state, attempt_count
        ) VALUES (
            'artifact_cleanup_' || md5(random()::text || clock_timestamp()::text),
            OLD.source_task_id, OLD.artifact_id, OLD.storage_type, OLD.storage_scope,
            OLD.storage_ref, 'artifact_storage_reference_replaced', 'pending', 0
        )
        ON CONFLICT (storage_type, storage_scope, storage_ref) DO UPDATE SET
            task_id = EXCLUDED.task_id, artifact_id = EXCLUDED.artifact_id,
            reason_code = EXCLUDED.reason_code, state = 'pending', attempt_count = 0,
            last_error = NULL, next_attempt_at = NOW(), lease_owner = NULL,
            lease_expires_at = NULL, terminal_at = NULL, updated_at = NOW();
    END IF;

    NEW.storage_type := lower(btrim(NEW.storage_type));
    NEW.storage_scope := btrim(COALESCE(NEW.storage_scope, ''));
    NEW.storage_ref := btrim(NEW.storage_ref);
    NEW.source_task_id := btrim(NEW.source_task_id);

    IF NEW.storage_type NOT IN ('inline', 'external', 'local')
       OR NEW.storage_ref = '' OR NEW.source_task_id = '' THEN
        RAISE EXCEPTION 'artifact_storage_identity_invalid' USING ERRCODE = '22023';
    END IF;
    IF NEW.storage_type = 'local' AND (
        NEW.storage_scope <> NEW.source_task_id
        OR NEW.storage_ref LIKE '/%'
        OR NEW.storage_ref LIKE '%/'
        OR NEW.storage_ref LIKE '%//%'
        OR strpos(NEW.storage_ref, E'\\') > 0
        OR NEW.storage_ref ~ '(^|/)(\.|\.\.)(/|$)'
    ) THEN
        RAISE EXCEPTION 'artifact_storage_identity_invalid' USING ERRCODE = '22023';
    END IF;
    IF NEW.storage_type = 'external' AND NEW.storage_scope = '' THEN
        RAISE EXCEPTION 'artifact_storage_identity_invalid' USING ERRCODE = '22023';
    END IF;
    IF NEW.storage_type = 'inline' THEN
        IF NEW.storage_scope <> NEW.artifact_id
           OR NEW.storage_ref !~ '^[0-9A-Fa-f]{64}$' THEN
            RAISE EXCEPTION 'artifact_storage_identity_invalid' USING ERRCODE = '22023';
        END IF;
        RETURN NEW;
    END IF;

    PERFORM pg_advisory_xact_lock(
        openair_artifact_storage_lock_key(
            NEW.storage_type, NEW.storage_scope, NEW.storage_ref
        )
    );
    SELECT state, reason_code, artifact_id
      INTO cleanup_row
      FROM openair_artifact_cleanup_outbox
     WHERE storage_type = NEW.storage_type
       AND storage_scope = NEW.storage_scope
       AND storage_ref = NEW.storage_ref;
    IF FOUND THEN
        IF cleanup_row.reason_code = 'upload_inflight_recovery'
           AND cleanup_row.state = 'pending'
           AND (cleanup_row.artifact_id IS NULL OR cleanup_row.artifact_id = NEW.artifact_id) THEN
            UPDATE openair_artifact_cleanup_outbox
               SET state = 'preserved', last_error = NULL, lease_owner = NULL,
                   lease_expires_at = NULL, terminal_at = NOW(), updated_at = NOW()
             WHERE storage_type = NEW.storage_type
               AND storage_scope = NEW.storage_scope
               AND storage_ref = NEW.storage_ref
               AND state = 'pending';
        ELSIF cleanup_row.state IN ('pending', 'claimed', 'completed', 'failed') THEN
            RAISE EXCEPTION 'artifact_storage_identity_retired' USING ERRCODE = '55000';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_reference
    ON openair_artifact_storage_references;
CREATE TRIGGER trg_openair_fence_artifact_storage_reference
BEFORE INSERT OR UPDATE OF storage_type, storage_scope, storage_ref
ON openair_artifact_storage_references
FOR EACH ROW EXECUTE FUNCTION openair_fence_artifact_storage_reference();

CREATE OR REPLACE FUNCTION openair_sync_artifact_storage_references()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    desired_keys TEXT[] := ARRAY[]::TEXT[];
    reference_key TEXT;
    version JSONB;
    version_id_value TEXT;
    storage_type_value TEXT;
    storage_scope_value TEXT;
    storage_ref_value TEXT;
    content_sha_value TEXT;
    content_size_value BIGINT;
    existing_ref RECORD;
BEGIN
    IF lower(btrim(COALESCE(NEW.storage_type, ''))) IN ('external', 'local')
       AND btrim(COALESCE(NEW.storage_ref, '')) <> '' THEN
        storage_type_value := lower(btrim(NEW.storage_type));
        storage_scope_value := CASE
            WHEN storage_type_value = 'local' THEN btrim(COALESCE(NEW.source_task_id, ''))
            ELSE btrim(COALESCE(NEW.storage_scope, ''))
        END;
        IF storage_type_value = 'external' AND storage_scope_value = '' THEN
            RAISE EXCEPTION 'artifact_external_storage_scope_required' USING ERRCODE = '22023';
        END IF;
        storage_ref_value := btrim(NEW.storage_ref);
        reference_key := 'current' || chr(31);
        desired_keys := array_append(desired_keys, reference_key);
        INSERT INTO openair_artifact_storage_references (
            artifact_id, reference_kind, version_id, source_task_id,
            storage_type, storage_scope, storage_ref, content_sha256, content_size, updated_at
        ) VALUES (
            NEW.artifact_id, 'current', '', NEW.source_task_id,
            storage_type_value, storage_scope_value, storage_ref_value,
            lower(NEW.content_sha256), NEW.content_size, NOW()
        )
        ON CONFLICT (artifact_id, reference_kind, version_id) DO UPDATE SET
            source_task_id = EXCLUDED.source_task_id,
            storage_type = EXCLUDED.storage_type,
            storage_scope = EXCLUDED.storage_scope,
            storage_ref = EXCLUDED.storage_ref,
            content_sha256 = EXCLUDED.content_sha256,
            content_size = EXCLUDED.content_size,
            updated_at = NOW();
    ELSIF lower(btrim(COALESCE(NEW.storage_type, ''))) = 'inline'
       AND COALESCE(NEW.content_sha256, '') ~ '^[0-9A-Fa-f]{64}$'
       AND NEW.content_size IS NOT NULL AND NEW.content_size >= 0 THEN
        reference_key := 'current' || chr(31);
        desired_keys := array_append(desired_keys, reference_key);
        INSERT INTO openair_artifact_storage_references (
            artifact_id, reference_kind, version_id, source_task_id,
            storage_type, storage_scope, storage_ref, content_sha256, content_size, updated_at
        ) VALUES (
            NEW.artifact_id, 'current', '', NEW.source_task_id,
            'inline', NEW.artifact_id, lower(NEW.content_sha256),
            lower(NEW.content_sha256), NEW.content_size, NOW()
        )
        ON CONFLICT (artifact_id, reference_kind, version_id) DO UPDATE SET
            source_task_id = EXCLUDED.source_task_id,
            storage_type = EXCLUDED.storage_type,
            storage_scope = EXCLUDED.storage_scope,
            storage_ref = EXCLUDED.storage_ref,
            content_sha256 = EXCLUDED.content_sha256,
            content_size = EXCLUDED.content_size,
            updated_at = NOW();
    END IF;

    FOR version IN
        SELECT value FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(NEW.metadata->'versions') = 'array'
                 THEN NEW.metadata->'versions' ELSE '[]'::jsonb END
        ) AS rows(value)
    LOOP
        version_id_value := btrim(COALESCE(version->>'versionId', ''));
        storage_type_value := lower(btrim(COALESCE(version->>'storageType', '')));
        content_sha_value := lower(btrim(COALESCE(version->>'contentSha256', '')));
        IF version_id_value = ''
           OR storage_type_value NOT IN ('inline', 'external', 'local')
           OR content_sha_value !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(version->'contentSize') <> 'number'
           OR (version->>'contentSize') !~ '^[0-9]+$' THEN
            CONTINUE;
        END IF;
        content_size_value := (version->>'contentSize')::BIGINT;
        storage_scope_value := CASE
            WHEN storage_type_value = 'local' THEN btrim(COALESCE(NEW.source_task_id, ''))
            WHEN storage_type_value = 'inline' THEN NEW.artifact_id
            ELSE btrim(COALESCE(version->>'storageScope', '')) END;
        IF storage_type_value = 'external' AND storage_scope_value = '' THEN
            CONTINUE;
        END IF;
        storage_ref_value := CASE
            WHEN storage_type_value = 'external' THEN btrim(COALESCE(version->>'externalKey', ''))
            WHEN storage_type_value = 'local' THEN btrim(COALESCE(version->>'path', ''))
            ELSE content_sha_value END;
        IF storage_ref_value = '' THEN
            CONTINUE;
        END IF;
        reference_key := 'historical' || chr(31) || version_id_value;
        IF reference_key = ANY(desired_keys) THEN
            RAISE EXCEPTION 'artifact_version_id_duplicate' USING ERRCODE = '23505';
        END IF;
        desired_keys := array_append(desired_keys, reference_key);
        INSERT INTO openair_artifact_storage_references (
            artifact_id, reference_kind, version_id, source_task_id,
            storage_type, storage_scope, storage_ref, content_sha256, content_size, updated_at
        ) VALUES (
            NEW.artifact_id, 'historical', version_id_value, NEW.source_task_id,
            storage_type_value, storage_scope_value, storage_ref_value,
            content_sha_value, content_size_value, NOW()
        )
        ON CONFLICT (artifact_id, reference_kind, version_id) DO NOTHING;
    END LOOP;

    FOR existing_ref IN
        SELECT * FROM openair_artifact_storage_references
         WHERE artifact_id = NEW.artifact_id
           AND (reference_kind || chr(31) || version_id) <> ALL(desired_keys)
         FOR UPDATE
    LOOP
        IF existing_ref.storage_type IN ('external', 'local') THEN
            INSERT INTO openair_artifact_cleanup_outbox (
                cleanup_id, task_id, artifact_id, storage_type, storage_scope,
                storage_ref, reason_code, state, attempt_count
            ) VALUES (
                'artifact_cleanup_' || md5(random()::text || clock_timestamp()::text),
                existing_ref.source_task_id, NEW.artifact_id,
                existing_ref.storage_type, existing_ref.storage_scope,
                existing_ref.storage_ref, 'artifact_storage_reference_retired', 'pending', 0
            )
            ON CONFLICT (storage_type, storage_scope, storage_ref) DO UPDATE SET
                task_id = EXCLUDED.task_id,
                artifact_id = EXCLUDED.artifact_id,
                reason_code = EXCLUDED.reason_code,
                state = 'pending', attempt_count = 0, last_error = NULL,
                next_attempt_at = NOW(), lease_owner = NULL,
                lease_expires_at = NULL, terminal_at = NULL, updated_at = NOW();
        END IF;
        DELETE FROM openair_artifact_storage_references
         WHERE artifact_id = existing_ref.artifact_id
           AND reference_kind = existing_ref.reference_kind
           AND version_id = existing_ref.version_id;
    END LOOP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_sync_artifact_storage_references ON openair_artifacts;
CREATE TRIGGER trg_openair_sync_artifact_storage_references
AFTER INSERT OR UPDATE OF storage_type, storage_scope, storage_ref, source_task_id,
    content_sha256, content_size, metadata
ON openair_artifacts
FOR EACH ROW EXECUTE FUNCTION openair_sync_artifact_storage_references();

CREATE OR REPLACE FUNCTION openair_retire_artifact_storage_references()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    existing_ref RECORD;
BEGIN
    FOR existing_ref IN
        SELECT * FROM openair_artifact_storage_references
         WHERE artifact_id = OLD.artifact_id
         FOR UPDATE
    LOOP
        IF existing_ref.storage_type IN ('external', 'local') THEN
            INSERT INTO openair_artifact_cleanup_outbox (
                cleanup_id, task_id, artifact_id, storage_type, storage_scope,
                storage_ref, reason_code, state, attempt_count
            ) VALUES (
                'artifact_cleanup_' || md5(random()::text || clock_timestamp()::text),
                existing_ref.source_task_id, OLD.artifact_id,
                existing_ref.storage_type, existing_ref.storage_scope,
                existing_ref.storage_ref, 'artifact_deleted_object_cleanup_pending', 'pending', 0
            )
            ON CONFLICT (storage_type, storage_scope, storage_ref) DO UPDATE SET
                task_id = EXCLUDED.task_id,
                artifact_id = EXCLUDED.artifact_id,
                reason_code = EXCLUDED.reason_code,
                state = 'pending', attempt_count = 0, last_error = NULL,
                next_attempt_at = NOW(), lease_owner = NULL,
                lease_expires_at = NULL, terminal_at = NULL, updated_at = NOW();
        END IF;
    END LOOP;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_retire_artifact_storage_references ON openair_artifacts;
CREATE TRIGGER trg_openair_retire_artifact_storage_references
BEFORE DELETE ON openair_artifacts
FOR EACH ROW EXECUTE FUNCTION openair_retire_artifact_storage_references();

-- The canonical reference table now owns the shared writer/cleanup fence.
DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_registration ON openair_artifacts;

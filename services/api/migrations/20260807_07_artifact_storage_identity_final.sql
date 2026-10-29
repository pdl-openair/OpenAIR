-- Line C round 5 final storage contract.
-- Migration runner is the sole DDL/backfill authority for this schema.

ALTER TABLE openair_artifacts
    ADD COLUMN IF NOT EXISTS storage_scope TEXT NULL;

-- Upgrade from the v4 fence atomically. Existing v4 triggers reject bucket-aware
-- external scopes, so suspend them only inside this migration transaction while
-- table locks prevent concurrent catalog mutation. PostgreSQL DDL is transactional:
-- any later failure restores the prior trigger set as well.
LOCK TABLE openair_artifacts IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE openair_artifact_storage_references IN SHARE ROW EXCLUSIVE MODE;
DROP TRIGGER IF EXISTS trg_openair_sync_artifact_storage_references ON openair_artifacts;
DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_reference
    ON openair_artifact_storage_references;
DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_registration ON openair_artifacts;

CREATE TABLE IF NOT EXISTS openair_artifact_storage_runtime (
    runtime_key   TEXT PRIMARY KEY,
    storage_scope TEXT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (runtime_key = 'minio_current'),
    CHECK (btrim(storage_scope) <> '')
);

-- Round-4 inline backfill used scope=''. Canonical inline identity is artifact_id + digest.
UPDATE openair_artifact_storage_references
   SET storage_scope = artifact_id, updated_at = NOW()
 WHERE storage_type = 'inline'
   AND storage_scope = '';

-- Existing external scope='' cannot prove its historical bucket. Never guess from current config.
UPDATE openair_artifact_cleanup_outbox
   SET state = 'failed',
       last_error = 'legacy_external_bucket_unknown',
       lease_owner = NULL,
       lease_expires_at = NULL,
       terminal_at = COALESCE(terminal_at, NOW()),
       updated_at = NOW()
 WHERE storage_type = 'external'
   AND btrim(COALESCE(storage_scope, '')) = ''
   AND state IN ('pending', 'claimed');

DELETE FROM openair_artifact_storage_references
 WHERE storage_type = 'external'
   AND btrim(COALESCE(storage_scope, '')) = '';

-- No legacy external row is backfilled from mutable runtime configuration or
-- arbitrary metadata. A pre-v5 object without a previously durable bucket scope
-- remains unavailable and non-deletable until an explicit reconciliation migration
-- supplies authoritative ownership evidence.

CREATE OR REPLACE FUNCTION openair_assign_artifact_storage_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    configured_scope TEXT;
BEGIN
    NEW.storage_type := lower(btrim(COALESCE(NEW.storage_type, '')));
    NEW.storage_scope := NULLIF(btrim(COALESCE(NEW.storage_scope, '')), '');

    IF NEW.storage_type = 'external' AND btrim(COALESCE(NEW.storage_ref, '')) <> '' THEN
        IF NEW.storage_scope IS NULL THEN
            IF TG_OP = 'UPDATE'
               AND lower(btrim(COALESCE(OLD.storage_type, ''))) = 'external'
               AND OLD.storage_ref IS NOT DISTINCT FROM NEW.storage_ref THEN
                -- Preserve a proven prior scope. Legacy unknown remains unknown/fail-closed.
                NEW.storage_scope := NULLIF(btrim(COALESCE(OLD.storage_scope, '')), '');
            ELSE
                SELECT storage_scope INTO configured_scope
                  FROM openair_artifact_storage_runtime
                 WHERE runtime_key = 'minio_current';
                IF configured_scope IS NULL OR btrim(configured_scope) = '' THEN
                    RAISE EXCEPTION 'artifact_external_storage_scope_required'
                        USING ERRCODE = '55000';
                END IF;
                NEW.storage_scope := btrim(configured_scope);
            END IF;
        END IF;
    ELSIF NEW.storage_type = 'local' AND btrim(COALESCE(NEW.storage_ref, '')) <> '' THEN
        NEW.storage_scope := NULLIF(btrim(COALESCE(NEW.source_task_id, '')), '');
    ELSIF NEW.storage_type = 'inline' THEN
        NEW.storage_scope := NEW.artifact_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_assign_artifact_storage_scope ON openair_artifacts;
CREATE TRIGGER trg_openair_assign_artifact_storage_scope
BEFORE INSERT OR UPDATE OF storage_type, storage_scope, storage_ref, source_task_id
ON openair_artifacts
FOR EACH ROW EXECUTE FUNCTION openair_assign_artifact_storage_scope();

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
        IF FOUND AND (
            OLD.source_task_id IS DISTINCT FROM NEW.source_task_id
            OR old_version->>'storageType' IS DISTINCT FROM new_version->>'storageType'
            OR old_version->>'storageScope' IS DISTINCT FROM new_version->>'storageScope'
            OR old_version->>'externalKey' IS DISTINCT FROM new_version->>'externalKey'
            OR old_version->>'path' IS DISTINCT FROM new_version->>'path'
            OR old_version->>'inlineContent' IS DISTINCT FROM new_version->>'inlineContent'
            OR old_version->>'contentSha256' IS DISTINCT FROM new_version->>'contentSha256'
            OR old_version->'contentSize' IS DISTINCT FROM new_version->'contentSize'
        ) THEN
            RAISE EXCEPTION 'artifact_historical_version_immutable'
                USING ERRCODE = '55000';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_enforce_historical_version_immutability ON openair_artifacts;
CREATE TRIGGER trg_openair_enforce_historical_version_immutability
BEFORE UPDATE OF metadata, source_task_id
ON openair_artifacts
FOR EACH ROW EXECUTE FUNCTION openair_enforce_historical_version_immutability();

CREATE OR REPLACE FUNCTION openair_enforce_historical_storage_reference_immutability()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.reference_kind = 'historical' AND (
        OLD.artifact_id IS DISTINCT FROM NEW.artifact_id
        OR OLD.reference_kind IS DISTINCT FROM NEW.reference_kind
        OR OLD.version_id IS DISTINCT FROM NEW.version_id
        OR OLD.source_task_id IS DISTINCT FROM NEW.source_task_id
        OR OLD.storage_type IS DISTINCT FROM NEW.storage_type
        OR OLD.storage_scope IS DISTINCT FROM NEW.storage_scope
        OR OLD.storage_ref IS DISTINCT FROM NEW.storage_ref
        OR OLD.content_sha256 IS DISTINCT FROM NEW.content_sha256
        OR OLD.content_size IS DISTINCT FROM NEW.content_size
    ) THEN
        RAISE EXCEPTION 'artifact_historical_version_immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_enforce_historical_storage_reference_immutability
    ON openair_artifact_storage_references;
CREATE TRIGGER trg_openair_enforce_historical_storage_reference_immutability
BEFORE UPDATE ON openair_artifact_storage_references
FOR EACH ROW EXECUTE FUNCTION openair_enforce_historical_storage_reference_immutability();

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
        RAISE EXCEPTION 'artifact_external_storage_scope_required' USING ERRCODE = '22023';
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
    old_version JSONB;
    version_id_value TEXT;
    storage_type_value TEXT;
    storage_scope_value TEXT;
    storage_ref_value TEXT;
    content_sha_value TEXT;
    content_size_value BIGINT;
    existing_ref RECORD;
    old_version_exists BOOLEAN;
BEGIN
    IF lower(btrim(COALESCE(NEW.storage_type, ''))) IN ('external', 'local')
       AND btrim(COALESCE(NEW.storage_ref, '')) <> '' THEN
        storage_type_value := lower(btrim(NEW.storage_type));
        storage_scope_value := CASE
            WHEN storage_type_value = 'local' THEN btrim(COALESCE(NEW.source_task_id, ''))
            ELSE btrim(COALESCE(NEW.storage_scope, ''))
        END;
        IF storage_type_value <> 'external' OR storage_scope_value <> '' THEN
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
        END IF;
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
        IF version_id_value = '' THEN
            CONTINUE;
        END IF;
        reference_key := 'historical' || chr(31) || version_id_value;
        IF reference_key = ANY(desired_keys) THEN
            RAISE EXCEPTION 'artifact_version_id_duplicate' USING ERRCODE = '23505';
        END IF;

        old_version_exists := FALSE;
        IF TG_OP = 'UPDATE' THEN
            SELECT value INTO old_version
              FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(OLD.metadata->'versions') = 'array'
                       THEN OLD.metadata->'versions' ELSE '[]'::jsonb END
              ) AS rows(value)
             WHERE btrim(COALESCE(value->>'versionId', '')) = version_id_value
             LIMIT 1;
            old_version_exists := FOUND;
        END IF;

        storage_type_value := lower(btrim(COALESCE(version->>'storageType', '')));
        content_sha_value := lower(btrim(COALESCE(version->>'contentSha256', '')));
        IF storage_type_value NOT IN ('inline', 'external', 'local')
           OR content_sha_value !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(version->'contentSize') <> 'number'
           OR (version->>'contentSize') !~ '^[0-9]+$' THEN
            -- Existing legacy versions with incomplete immutable evidence remain
            -- unavailable; the BEFORE trigger prevents any in-place mutation.
            CONTINUE;
        END IF;
        content_size_value := (version->>'contentSize')::BIGINT;
        storage_scope_value := CASE
            WHEN storage_type_value = 'local' THEN btrim(COALESCE(NEW.source_task_id, ''))
            WHEN storage_type_value = 'inline' THEN NEW.artifact_id
            ELSE btrim(COALESCE(version->>'storageScope', ''))
        END;
        storage_ref_value := CASE
            WHEN storage_type_value = 'external' THEN btrim(COALESCE(version->>'externalKey', ''))
            WHEN storage_type_value = 'local' THEN btrim(COALESCE(version->>'path', ''))
            ELSE content_sha_value
        END;
        IF storage_ref_value = '' OR (storage_type_value = 'external' AND storage_scope_value = '') THEN
            -- Existing legacy external versions without a proven bucket stay
            -- catalog-visible but deliberately have no readable/deletable reference.
            IF old_version_exists THEN
                CONTINUE;
            END IF;
            IF storage_type_value = 'external' THEN
                RAISE EXCEPTION 'artifact_external_storage_scope_required' USING ERRCODE = '55000';
            END IF;
            CONTINUE;
        END IF;

        desired_keys := array_append(desired_keys, reference_key);
        SELECT * INTO existing_ref
          FROM openair_artifact_storage_references
         WHERE artifact_id = NEW.artifact_id
           AND reference_kind = 'historical'
           AND version_id = version_id_value
         FOR UPDATE;
        IF FOUND THEN
            IF existing_ref.source_task_id IS DISTINCT FROM NEW.source_task_id
               OR existing_ref.storage_type IS DISTINCT FROM storage_type_value
               OR existing_ref.storage_scope IS DISTINCT FROM storage_scope_value
               OR existing_ref.storage_ref IS DISTINCT FROM storage_ref_value
               OR existing_ref.content_sha256 IS DISTINCT FROM content_sha_value
               OR existing_ref.content_size IS DISTINCT FROM content_size_value THEN
                RAISE EXCEPTION 'artifact_historical_version_immutable' USING ERRCODE = '55000';
            END IF;
        ELSE
            INSERT INTO openair_artifact_storage_references (
                artifact_id, reference_kind, version_id, source_task_id,
                storage_type, storage_scope, storage_ref, content_sha256, content_size, updated_at
            ) VALUES (
                NEW.artifact_id, 'historical', version_id_value, NEW.source_task_id,
                storage_type_value, storage_scope_value, storage_ref_value,
                content_sha_value, content_size_value, NOW()
            )
            ON CONFLICT (artifact_id, reference_kind, version_id) DO NOTHING;
            IF NOT FOUND THEN
                SELECT * INTO existing_ref
                  FROM openair_artifact_storage_references
                 WHERE artifact_id = NEW.artifact_id
                   AND reference_kind = 'historical'
                   AND version_id = version_id_value
                 FOR UPDATE;
                IF existing_ref.source_task_id IS DISTINCT FROM NEW.source_task_id
                   OR existing_ref.storage_type IS DISTINCT FROM storage_type_value
                   OR existing_ref.storage_scope IS DISTINCT FROM storage_scope_value
                   OR existing_ref.storage_ref IS DISTINCT FROM storage_ref_value
                   OR existing_ref.content_sha256 IS DISTINCT FROM content_sha_value
                   OR existing_ref.content_size IS DISTINCT FROM content_size_value THEN
                    RAISE EXCEPTION 'artifact_historical_version_immutable' USING ERRCODE = '55000';
                END IF;
            END IF;
        END IF;
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

-- Line C round 3: serialize Artifact catalog registration with cleanup deletion.
--
-- Storage identity is (storage_type, storage_scope, storage_ref):
--   external: deployment-wide object bucket, scope=''
--   local:    task workspace, scope=source_task_id

ALTER TABLE openair_artifact_cleanup_outbox
    ADD COLUMN IF NOT EXISTS storage_scope TEXT NOT NULL DEFAULT '';

UPDATE openair_artifact_cleanup_outbox
   SET storage_scope = COALESCE(task_id, '')
 WHERE storage_type = 'local' AND storage_scope = '';

ALTER TABLE openair_artifact_cleanup_outbox
    DROP CONSTRAINT IF EXISTS openair_artifact_cleanup_outbox_storage_type_storage_ref_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_artifact_cleanup_identity
    ON openair_artifact_cleanup_outbox(storage_type, storage_scope, storage_ref);

-- A historical successful upload may have closed its recovery row as completed.
-- If the catalog still references that exact scoped identity, it was preserved.
UPDATE openair_artifact_cleanup_outbox AS outbox
   SET state = 'preserved', terminal_at = COALESCE(terminal_at, NOW()), updated_at = NOW()
 WHERE state = 'completed'
   AND EXISTS (
       SELECT 1
         FROM openair_artifacts AS artifact
        WHERE artifact.storage_type = outbox.storage_type
          AND artifact.storage_ref = outbox.storage_ref
          AND (
              outbox.storage_type <> 'local'
              OR artifact.source_task_id = outbox.storage_scope
          )
   );

CREATE OR REPLACE FUNCTION openair_artifact_storage_lock_key(
    storage_type_value TEXT,
    storage_scope_value TEXT,
    storage_ref_value TEXT
)
RETURNS BIGINT
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT hashtextextended(
        lower(btrim(COALESCE(storage_type_value, ''))) || chr(31) ||
        btrim(COALESCE(storage_scope_value, '')) || chr(31) ||
        btrim(COALESCE(storage_ref_value, '')),
        0
    );
$$;

CREATE OR REPLACE FUNCTION openair_fence_artifact_storage_registration()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    cleanup_row RECORD;
    storage_scope_value TEXT;
BEGIN
    IF lower(btrim(COALESCE(NEW.storage_type, ''))) IN ('external', 'local') THEN
        IF NEW.storage_type IS DISTINCT FROM lower(btrim(NEW.storage_type))
           OR NEW.storage_ref IS NULL
           OR NEW.storage_ref IS DISTINCT FROM btrim(NEW.storage_ref)
           OR NEW.storage_ref = '' THEN
            RAISE EXCEPTION 'artifact_storage_identity_invalid'
                USING ERRCODE = '22023';
        END IF;
        IF NEW.storage_type = 'local' AND (
            NEW.storage_ref LIKE '/%'
            OR NEW.storage_ref LIKE '%/'
            OR NEW.storage_ref LIKE '%//%'
            OR strpos(NEW.storage_ref, E'\\') > 0
            OR NEW.storage_ref ~ '(^|/)(\.|\.\.)(/|$)'
        ) THEN
            RAISE EXCEPTION 'artifact_storage_identity_invalid'
                USING ERRCODE = '22023';
        END IF;

        storage_scope_value := CASE
            WHEN NEW.storage_type = 'local' THEN btrim(COALESCE(NEW.source_task_id, ''))
            ELSE ''
        END;
        IF NEW.storage_type = 'local' AND storage_scope_value = '' THEN
            RAISE EXCEPTION 'artifact_storage_identity_invalid'
                USING ERRCODE = '22023';
        END IF;

        PERFORM pg_advisory_xact_lock(
            openair_artifact_storage_lock_key(
                NEW.storage_type, storage_scope_value, NEW.storage_ref
            )
        );

        SELECT state, reason_code, artifact_id
          INTO cleanup_row
          FROM openair_artifact_cleanup_outbox
         WHERE storage_type = NEW.storage_type
           AND storage_scope = storage_scope_value
           AND storage_ref = NEW.storage_ref;

        IF FOUND THEN
            -- The exact upload that created a pre-write recovery reservation may
            -- atomically convert it to preserved. Retired/claimed identities cannot
            -- be reattached after cleanup has obtained deletion ownership.
            IF cleanup_row.reason_code = 'upload_inflight_recovery'
               AND cleanup_row.state = 'pending'
               AND (cleanup_row.artifact_id IS NULL OR cleanup_row.artifact_id = NEW.artifact_id) THEN
                UPDATE openair_artifact_cleanup_outbox
                   SET state = 'preserved', last_error = NULL,
                       lease_owner = NULL, lease_expires_at = NULL,
                       terminal_at = NOW(), updated_at = NOW()
                 WHERE storage_type = NEW.storage_type
                   AND storage_scope = storage_scope_value
                   AND storage_ref = NEW.storage_ref
                   AND state = 'pending';
            ELSIF cleanup_row.state IN ('pending', 'claimed', 'completed', 'failed') THEN
                RAISE EXCEPTION 'artifact_storage_identity_retired'
                    USING ERRCODE = '55000';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_registration
    ON openair_artifacts;
CREATE TRIGGER trg_openair_fence_artifact_storage_registration
BEFORE INSERT OR UPDATE OF storage_type, storage_ref, source_task_id
ON openair_artifacts
FOR EACH ROW
EXECUTE FUNCTION openair_fence_artifact_storage_registration();

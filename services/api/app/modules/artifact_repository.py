"""``openair_artifacts`` 一等实体仓库（Artifact-03，Option-B 延迟项）。

镜像 ``task_repository.py`` 的 psycopg 风格（``_connect`` ctx + ``CREATE TABLE IF NOT EXISTS``
+ ``ON CONFLICT`` upsert + ``dict_row``）。产物仍嵌在 task payload（写入漏斗在
``task_store.add_artifact``/``remove_artifact``），本仓库作为 catalog 读源 + 一等实体存储。
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Literal
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.models.result_protocol import artifact_quality_for_protocol, normalize_result_protocol
from app.modules.artifact_gate import apply_artifact_gate, normalize_artifact_state
from app.modules.research_task_lifecycle import ArtifactLifecycleFact
from app.modules.research_task_lifecycle_repository import (
    TaskLifecycleWriteConflictError,
    commit_task_lifecycle_mutation_locked,
)


def _iso(value: Any) -> str | None:
    """psycopg returns TIMESTAMPTZ as datetime; catalog/response models want ISO str."""
    return value.isoformat() if isinstance(value, datetime) else value

ARTIFACTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_artifacts (
    artifact_id              TEXT PRIMARY KEY,
    owner_id                 TEXT NOT NULL,
    workspace_id             TEXT NOT NULL,
    created_by               TEXT NULL,
    kind                     TEXT NOT NULL,
    origin                   TEXT NULL,
    title                    TEXT NOT NULL,
    summary                  TEXT NULL,
    format                   TEXT NULL,
    mime_type                TEXT NULL,
    storage_type             TEXT NULL,
    storage_scope            TEXT NULL,
    storage_ref              TEXT NULL,
    external_provider        TEXT NULL,
    external_uri             TEXT NULL,
    content_size             BIGINT NULL,
    content_sha256           TEXT NULL,
    source_task_id           TEXT NULL,
    source_task_title        TEXT NULL,
    source_capability_call_id TEXT NULL,
    related_stage            TEXT NULL,
    related_skill_id         TEXT NULL,
    metadata                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at               TIMESTAMPTZ NULL,
    updated_at               TIMESTAMPTZ NULL,
    archived_at              TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_scope ON openair_artifacts(owner_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_task ON openair_artifacts(source_task_id);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_kind ON openair_artifacts(kind);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_origin ON openair_artifacts(origin);
"""

ARTIFACT_CLEANUP_OUTBOX_SQL = """
CREATE TABLE IF NOT EXISTS openair_artifact_cleanup_outbox (
    cleanup_id      TEXT PRIMARY KEY,
    task_id         TEXT NULL,
    artifact_id     TEXT NULL,
    storage_type    TEXT NOT NULL,
    storage_scope   TEXT NOT NULL DEFAULT '',
    storage_ref     TEXT NOT NULL,
    reason_code     TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'pending',
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT NULL,
    lease_owner     TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    terminal_at     TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (storage_type, storage_scope, storage_ref)
);
ALTER TABLE openair_artifact_cleanup_outbox
    ADD COLUMN IF NOT EXISTS storage_scope TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS terminal_at TIMESTAMPTZ;
UPDATE openair_artifact_cleanup_outbox
   SET storage_scope = COALESCE(task_id, '')
 WHERE storage_type = 'local' AND storage_scope = '';
ALTER TABLE openair_artifact_cleanup_outbox
    DROP CONSTRAINT IF EXISTS openair_artifact_cleanup_outbox_storage_type_storage_ref_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_artifact_cleanup_identity
    ON openair_artifact_cleanup_outbox(storage_type, storage_scope, storage_ref);
DROP INDEX IF EXISTS idx_openair_artifact_cleanup_pending;
CREATE INDEX idx_openair_artifact_cleanup_pending
    ON openair_artifact_cleanup_outbox(state, next_attempt_at, lease_expires_at, cleanup_id);
"""

DELETION_RECEIPTS_SQL = """
CREATE TABLE IF NOT EXISTS openair_user_delete_receipts (
    target_type  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id      TEXT NULL,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (target_type, target_id, owner_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_openair_user_delete_receipts_task
    ON openair_user_delete_receipts(task_id, owner_id, workspace_id);
"""

ARTIFACT_STORAGE_FENCE_SQL = """
CREATE OR REPLACE FUNCTION openair_artifact_storage_lock_key(
    storage_type_value TEXT, storage_scope_value TEXT, storage_ref_value TEXT
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
            OR NEW.storage_ref ~ '(^|/)(\\.|\\.\\.)(/|$)'
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
            IF cleanup_row.reason_code = 'upload_inflight_recovery'
               AND cleanup_row.state = 'pending'
               AND (cleanup_row.artifact_id IS NULL OR cleanup_row.artifact_id = NEW.artifact_id) THEN
                UPDATE openair_artifact_cleanup_outbox
                   SET state = 'preserved', last_error = NULL, lease_owner = NULL,
                       lease_expires_at = NULL, terminal_at = NOW(), updated_at = NOW()
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

DROP TRIGGER IF EXISTS trg_openair_fence_artifact_storage_registration ON openair_artifacts;
CREATE TRIGGER trg_openair_fence_artifact_storage_registration
BEFORE INSERT OR UPDATE OF storage_type, storage_ref, source_task_id ON openair_artifacts
FOR EACH ROW EXECUTE FUNCTION openair_fence_artifact_storage_registration();
"""

ARTIFACT_STORAGE_REFERENCES_SQL = r"""

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
       CASE WHEN storage_type = 'local' THEN source_task_id ELSE '' END,
       CASE WHEN storage_type = 'inline' THEN content_sha256 ELSE storage_ref END,
       content_sha256, content_size
  FROM openair_artifacts
 WHERE source_task_id IS NOT NULL
   AND btrim(source_task_id) <> ''
   AND (
       (storage_type IN ('external', 'local') AND storage_ref IS NOT NULL AND btrim(storage_ref) <> '')
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
       CASE WHEN lower(btrim(version.value->>'storageType')) = 'local'
            THEN artifact.source_task_id ELSE '' END,
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
        AND btrim(COALESCE(version.value->>'externalKey', '')) <> '')
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
    IF NEW.storage_type = 'external' AND NEW.storage_scope <> '' THEN
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
        storage_scope_value := CASE WHEN storage_type_value = 'local'
            THEN btrim(COALESCE(NEW.source_task_id, '')) ELSE '' END;
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
            ELSE '' END;
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
        ON CONFLICT (artifact_id, reference_kind, version_id) DO UPDATE SET
            source_task_id = EXCLUDED.source_task_id,
            storage_type = EXCLUDED.storage_type,
            storage_scope = EXCLUDED.storage_scope,
            storage_ref = EXCLUDED.storage_ref,
            content_sha256 = EXCLUDED.content_sha256,
            content_size = EXCLUDED.content_size,
            updated_at = NOW();
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
AFTER INSERT OR UPDATE OF storage_type, storage_ref, source_task_id,
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
"""

UPSERT_ARTIFACT_SQL = """
INSERT INTO openair_artifacts (
    artifact_id, owner_id, workspace_id, created_by, kind, origin, title, summary,
    format, mime_type, storage_type, storage_scope, storage_ref, external_provider, external_uri,
    content_size, content_sha256, source_task_id, source_task_title,
    source_capability_call_id, related_stage, related_skill_id, metadata,
    created_at, updated_at, archived_at
) VALUES (
    %(artifact_id)s, %(owner_id)s, %(workspace_id)s, %(created_by)s, %(kind)s, %(origin)s,
    %(title)s, %(summary)s, %(format)s, %(mime_type)s, %(storage_type)s, %(storage_scope)s, %(storage_ref)s,
    %(external_provider)s, %(external_uri)s, %(content_size)s, %(content_sha256)s,
    %(source_task_id)s, %(source_task_title)s, %(source_capability_call_id)s,
    %(related_stage)s, %(related_skill_id)s, %(metadata)s::jsonb,
    %(created_at)s, %(updated_at)s, %(archived_at)s
)
ON CONFLICT (artifact_id) DO UPDATE SET
    owner_id = EXCLUDED.owner_id,
    workspace_id = EXCLUDED.workspace_id,
    created_by = EXCLUDED.created_by,
    kind = EXCLUDED.kind,
    origin = EXCLUDED.origin,
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    format = EXCLUDED.format,
    mime_type = EXCLUDED.mime_type,
    storage_type = EXCLUDED.storage_type,
    storage_scope = EXCLUDED.storage_scope,
    storage_ref = EXCLUDED.storage_ref,
    external_provider = EXCLUDED.external_provider,
    external_uri = EXCLUDED.external_uri,
    content_size = EXCLUDED.content_size,
    content_sha256 = EXCLUDED.content_sha256,
    source_task_id = EXCLUDED.source_task_id,
    source_task_title = EXCLUDED.source_task_title,
    source_capability_call_id = EXCLUDED.source_capability_call_id,
    related_stage = EXCLUDED.related_stage,
    related_skill_id = EXCLUDED.related_skill_id,
    metadata = EXCLUDED.metadata,
    created_at = EXCLUDED.created_at,
    updated_at = EXCLUDED.updated_at,
archived_at = EXCLUDED.archived_at;
"""
INSERT_ARTIFACT_SQL = UPSERT_ARTIFACT_SQL.split("ON CONFLICT", 1)[0].rstrip()

DELETE_ARTIFACT_SQL = "DELETE FROM openair_artifacts WHERE artifact_id = %s"


def _storage_ref(artifact: dict[str, Any]) -> str | None:
    """统一存储指针：inline→inlineContent；external→externalKey；local→path；object→contentSha256。"""
    st = artifact.get("storageType")
    if st == "inline":
        return artifact.get("inlineContent")
    if st == "external":
        return artifact.get("externalKey")
    if st == "object":
        # Content-addressed object store: the blob key is its sha256.
        return artifact.get("contentSha256")
    return artifact.get("path")


def _storage_scope(
    storage_type: str, task_id: str | None, storage_scope: str | None = None
) -> str:
    if storage_type == "external":
        normalized_scope = str(storage_scope or "").strip()
        if not normalized_scope:
            raise ValueError("artifact_external_storage_scope_required")
        return normalized_scope
    if storage_type == "local":
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("artifact_cleanup_task_missing")
        return normalized_task_id
    raise ValueError("artifact_cleanup_reference_invalid")



def _metadata_from_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    out = dict(metadata)
    for key in (
        "evidenceRefs",
        "linkedMaterialIds",
        "materialManifest",
        "contentManifest",
        "memoryRefs",
        "trustRecords",
        "taskId",
        "stage",
        "subStageInstanceId",
        "substage",
        "artifactState",
        "validatorResult",
        "resultProtocol",
        "artifactQuality",
        "warnings",
        "humanGate",
        "artifactType",
        "sourceOutputPath",
        "reviewedAt",
        "reviewedBy",
        "reviewSource",
        "reviewNote",
        "artifactVisibility",
        "audience",
        "sourceMode",
        "currentVersionId",
        "versions",
        "revisionRequestedAt",
        "revisionFeedback",
        "structuredResearchStart",
        "researchStartConfirmation",
        "missingInputs",
        "nonCitableBoundaries",
        "downstreamInput",
    ):
        if key in artifact:
            out[key] = artifact.get(key)
    return out


def _version_snapshot_from_artifact(artifact: dict[str, Any]) -> dict[str, Any] | None:
    """Return an immutable, content-addressed snapshot when the row has enough evidence."""
    storage_type = str(artifact.get("storageType") or "").strip().lower()
    digest = str(artifact.get("contentSha256") or "").strip().lower()
    size = artifact.get("contentSize")
    if (
        storage_type not in {"inline", "external", "local", "object"}
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
    ):
        return None
    snapshot: dict[str, Any] = {
        "storageType": storage_type,
        "storageScope": artifact.get("storageScope"),
        "inlineContent": None,
        "path": None,
        "externalKey": None,
        "contentSha256": digest,
        "contentSize": size,
    }
    if storage_type == "inline":
        inline = artifact.get("inlineContent")
        if not isinstance(inline, str):
            return None
        blob = inline.encode("utf-8")
        if len(blob) != size or hashlib.sha256(blob).hexdigest() != digest:
            return None
        snapshot["inlineContent"] = inline
    elif storage_type == "external":
        key = str(artifact.get("externalKey") or "").strip()
        scope = str(artifact.get("storageScope") or "").strip()
        if not key or not scope:
            return None
        snapshot["externalKey"] = key
        snapshot["storageScope"] = scope
    elif storage_type == "local":
        path = str(artifact.get("path") or "").strip()
        if not path:
            return None
        snapshot["path"] = path
        snapshot["storageScope"] = str(artifact.get("sourceTaskId") or artifact.get("taskId") or "").strip() or None
    # object versions are immutable by their content-addressed sha256 identity.
    # They deliberately carry no cleanup-owned storage reference.
    return snapshot


def ensure_initial_version_truth(artifact: dict[str, Any]) -> dict[str, Any]:
    """Materialize deterministic V1 metadata for canonical content-bearing artifacts."""
    candidate = dict(artifact)
    versions = candidate.get("versions")
    if isinstance(versions, list) and versions:
        if not candidate.get("currentVersionId"):
            content_versions = [v for v in versions if isinstance(v, dict) and v.get("source") != "revision_requested"]
            if content_versions:
                candidate["currentVersionId"] = content_versions[-1].get("versionId")
        return candidate
    snapshot = _version_snapshot_from_artifact(candidate)
    artifact_id = str(candidate.get("id") or "").strip()
    if snapshot is None or not artifact_id:
        return candidate
    version_id = f"v_initial_{artifact_id}"
    created_at = candidate.get("createdAt")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    initial = {
        "versionId": version_id,
        "versionNumber": 1,
        "createdAt": created_at or datetime.now(timezone.utc).isoformat(),
        "createdBy": candidate.get("createdBy") or "system",
        "source": "initial",
        "feedback": None,
        "parentVersionId": None,
        "restoredFromVersionId": None,
        **snapshot,
    }
    candidate["versions"] = [initial]
    candidate["currentVersionId"] = version_id
    return candidate


class ArtifactRepository:
    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bootstrap(self) -> None:
        """Fail closed unless the migration runner has installed Line C schema.

        Repository bootstrap is deliberately read-only: it must never create tables,
        triggers, backfill storage references, or bypass postgres_migrations_auto_apply.
        """
        required_migration = "20260807_07_artifact_storage_identity_final.sql"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT
                           to_regclass('public.openair_artifacts') AS artifacts,
                           to_regclass('public.openair_artifact_cleanup_outbox') AS cleanup,
                           to_regclass('public.openair_user_delete_receipts') AS delete_receipts,
                           to_regclass('public.openair_artifact_storage_references') AS storage_refs,
                           to_regclass('public.openair_artifact_storage_runtime') AS storage_runtime,
                           to_regclass('public.openair_schema_migrations') AS migration_ledger"""
                )
                row = cur.fetchone() or {}
                if not all(row.get(key) for key in (
                    "artifacts", "cleanup", "delete_receipts", "storage_refs",
                    "storage_runtime", "migration_ledger",
                )):
                    raise RuntimeError(f"artifact_schema_migration_required:{required_migration}")
                cur.execute(
                    """SELECT EXISTS (
                           SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'openair_artifacts'
                              AND column_name = 'storage_scope'
                       ) AS storage_scope_ready,
                       EXISTS (
                           SELECT 1 FROM pg_trigger
                            WHERE tgname = 'trg_openair_sync_artifact_storage_references'
                              AND NOT tgisinternal
                       ) AS sync_trigger_ready,
                       EXISTS (
                           SELECT 1 FROM pg_trigger
                            WHERE tgname = 'trg_openair_enforce_historical_version_immutability'
                              AND NOT tgisinternal
                       ) AS immutable_trigger_ready"""
                )
                checks = cur.fetchone() or {}
                if not all(checks.get(key) for key in (
                    "storage_scope_ready", "sync_trigger_ready", "immutable_trigger_ready",
                )):
                    raise RuntimeError(f"artifact_schema_migration_required:{required_migration}")
                cur.execute(
                    "SELECT 1 FROM openair_schema_migrations WHERE version = %s",
                    (required_migration,),
                )
                if cur.fetchone() is None:
                    raise RuntimeError(f"artifact_schema_migration_required:{required_migration}")

    def configure_external_storage_scope(self, storage_scope: str) -> None:
        """Register the scope used for *new* external writes in this deployment.

        This does not backfill legacy artifacts. The INSERT trigger consumes this value
        only when a new external artifact lacks an explicit persisted scope.
        """
        normalized = str(storage_scope or "").strip()
        if not normalized:
            raise ValueError("artifact_external_storage_scope_required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO openair_artifact_storage_runtime(runtime_key, storage_scope)
                       VALUES ('minio_current', %s)
                       ON CONFLICT (runtime_key) DO UPDATE
                           SET storage_scope = EXCLUDED.storage_scope, updated_at = NOW()
                         WHERE openair_artifact_storage_runtime.storage_scope
                               IS DISTINCT FROM EXCLUDED.storage_scope""",
                    (normalized,),
                )

    def upsert_from_dict(self, task_id: str, artifact: dict[str, Any], task_title: str | None = None) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(UPSERT_ARTIFACT_SQL, self._artifact_insert_params(task_id, artifact, task_title))

    def insert_from_dict_if_absent(
        self, task_id: str | None, artifact: dict[str, Any], task_title: str | None = None
    ) -> bool:
        """Insert one Artifact identity once; return False when it already exists."""
        statement = f"{INSERT_ARTIFACT_SQL}\nON CONFLICT (artifact_id) DO NOTHING"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(statement, self._artifact_insert_params(task_id, artifact, task_title))
                return cur.rowcount == 1

    @staticmethod
    def _artifact_insert_params(
        task_id: str | None, artifact: dict[str, Any], task_title: str | None = None
    ) -> dict[str, Any]:
        artifact = ensure_initial_version_truth(artifact)
        return {
            "artifact_id": artifact.get("id"),
            "owner_id": artifact.get("ownerId"),
            "workspace_id": artifact.get("workspaceId"),
            "created_by": artifact.get("createdBy"),
            "kind": artifact.get("kind") or "artifact",
            "origin": artifact.get("origin"),
            "title": artifact.get("title") or "untitled",
            "summary": artifact.get("summary"),
            "format": artifact.get("format"),
            "mime_type": artifact.get("mimeType"),
            "storage_type": artifact.get("storageType"),
            "storage_scope": artifact.get("storageScope"),
            "storage_ref": _storage_ref(artifact),
            "external_provider": artifact.get("externalProvider"),
            "external_uri": artifact.get("externalUri"),
            "content_size": artifact.get("contentSize"),
            "content_sha256": artifact.get("contentSha256"),
            "source_task_id": artifact.get("sourceTaskId") or task_id,
            "source_task_title": task_title,
            "source_capability_call_id": artifact.get("sourceCapabilityCallId"),
            "related_stage": artifact.get("relatedStage") or artifact.get("stage"),
            "related_skill_id": artifact.get("relatedSkillId"),
            "metadata": json.dumps(_metadata_from_artifact(artifact), ensure_ascii=False),
            "created_at": artifact.get("createdAt"),
            "updated_at": artifact.get("updatedAt"),
            "archived_at": artifact.get("archivedAt"),
        }

    def insert_artifact_with_cursor(
        self, cur: Any, task_id: str, artifact: dict[str, Any], task_title: str | None = None
    ) -> None:
        """Plain INSERT using a caller-owned transaction; never upsert here."""
        cur.execute(INSERT_ARTIFACT_SQL, self._artifact_insert_params(task_id, artifact, task_title))

    def remove(self, artifact_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(DELETE_ARTIFACT_SQL, (artifact_id,))

    def enqueue_storage_cleanup(
        self,
        *,
        storage_type: str,
        storage_ref: str,
        reason_code: str,
        task_id: str | None = None,
        artifact_id: str | None = None,
        storage_scope: str | None = None,
        last_error: str | None = None,
        not_before_seconds: int = 0,
    ) -> None:
        """Durably record a compensating object/file deletion after DB commit."""
        normalized_type = storage_type.strip()
        normalized_ref = storage_ref.strip()
        if normalized_type not in {"external", "local"} or not normalized_ref:
            raise ValueError("artifact_cleanup_reference_invalid")
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._enqueue_storage_cleanup_with_cursor(
                    cur,
                    task_id=task_id,
                    artifact_id=artifact_id,
                    storage_type=normalized_type,
                    storage_ref=normalized_ref,
                    storage_scope=storage_scope,
                    reason_code=reason_code,
                    last_error=last_error,
                    not_before_seconds=not_before_seconds,
                )

    @staticmethod
    def _enqueue_storage_cleanup_with_cursor(
        cur: Any,
        *,
        storage_type: str,
        storage_ref: str,
        reason_code: str,
        task_id: str | None = None,
        artifact_id: str | None = None,
        storage_scope: str | None = None,
        last_error: str | None = None,
        not_before_seconds: int = 0,
    ) -> None:
        cur.execute(
            """INSERT INTO openair_artifact_cleanup_outbox (
                   cleanup_id, task_id, artifact_id, storage_type, storage_scope, storage_ref,
                   reason_code, state, attempt_count, last_error, next_attempt_at,
                   lease_owner, lease_expires_at, terminal_at
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 0, %s,
                         NOW() + (%s * INTERVAL '1 second'), NULL, NULL, NULL)
               ON CONFLICT (storage_type, storage_scope, storage_ref) DO UPDATE SET
                   task_id = COALESCE(EXCLUDED.task_id, openair_artifact_cleanup_outbox.task_id),
                   artifact_id = COALESCE(EXCLUDED.artifact_id, openair_artifact_cleanup_outbox.artifact_id),
                   reason_code = EXCLUDED.reason_code,
                   state = 'pending',
                   last_error = EXCLUDED.last_error,
                   next_attempt_at = EXCLUDED.next_attempt_at,
                   lease_owner = NULL,
                   lease_expires_at = NULL,
                   terminal_at = NULL,
                   updated_at = NOW()""",
            (
                f"artifact_cleanup_{uuid4().hex}", task_id, artifact_id, storage_type,
                _storage_scope(storage_type, task_id, storage_scope), storage_ref, reason_code,
                (last_error or "")[:1000] or None,
                max(0, int(not_before_seconds)),
            ),
        )

    def mark_storage_cleanup_completed(
        self, *, storage_type: str, storage_ref: str, task_id: str | None = None,
        storage_scope: str | None = None
    ) -> None:
        """Retire an identity after its durable object was idempotently deleted."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_artifact_cleanup_outbox
                          SET state = 'completed', attempt_count = attempt_count + 1,
                              last_error = NULL, lease_owner = NULL,
                              lease_expires_at = NULL, terminal_at = NOW(),
                              updated_at = NOW()
                        WHERE storage_type = %s AND storage_scope = %s AND storage_ref = %s""",
                    (storage_type, _storage_scope(storage_type, task_id, storage_scope), storage_ref),
                )

    def mark_storage_cleanup_preserved(
        self, *, storage_type: str, storage_ref: str, task_id: str | None = None,
        storage_scope: str | None = None
    ) -> None:
        """Close an upload reservation after the catalog owns the durable object."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_artifact_cleanup_outbox
                          SET state = 'preserved', last_error = NULL,
                              lease_owner = NULL, lease_expires_at = NULL,
                              terminal_at = NOW(), updated_at = NOW()
                        WHERE storage_type = %s AND storage_scope = %s AND storage_ref = %s
                          AND reason_code = 'upload_inflight_recovery'
                          AND state IN ('pending', 'preserved')""",
                    (storage_type, _storage_scope(storage_type, task_id, storage_scope), storage_ref),
                )

    def claim_storage_cleanup(
        self,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
        batch_size: int = 8,
    ) -> list[dict[str, Any]]:
        """Lease pending or expired cleanup rows with SKIP LOCKED recovery."""
        if not lease_owner.strip() or lease_seconds < 1 or batch_size < 1:
            raise ValueError("invalid artifact cleanup claim")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """WITH candidates AS (
                           SELECT cleanup_id
                             FROM openair_artifact_cleanup_outbox
                            WHERE (
                                state = 'pending'
                                OR (state = 'claimed' AND lease_expires_at <= NOW())
                            )
                              AND next_attempt_at <= NOW()
                            ORDER BY next_attempt_at, updated_at, cleanup_id
                            LIMIT %s
                            FOR UPDATE SKIP LOCKED
                       )
                       UPDATE openair_artifact_cleanup_outbox AS outbox
                          SET state = 'claimed', lease_owner = %s,
                              lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                              attempt_count = attempt_count + 1,
                              updated_at = NOW()
                         FROM candidates
                        WHERE outbox.cleanup_id = candidates.cleanup_id
                       RETURNING outbox.*""",
                    (batch_size, lease_owner, lease_seconds),
                )
                return [dict(row) for row in cur.fetchall()]

    def execute_storage_cleanup_claim(
        self,
        *,
        cleanup_id: str,
        lease_owner: str,
        delete_callback: Callable[[str, str, str, str], None],
    ) -> Literal["deleted", "preserved"]:
        """Hold the storage-identity advisory lock through check, delete and commit.

        The catalog trigger takes the same lock before every PostgreSQL artifact
        registration/update. A completed deletion retires the identity, so a writer
        that was blocked behind this critical section is rejected after the lock is
        released instead of attaching to bytes that were just deleted.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT cleanup_id, task_id, storage_type, storage_scope, storage_ref
                         FROM openair_artifact_cleanup_outbox
                        WHERE cleanup_id = %s AND state = 'claimed'
                          AND lease_owner = %s AND lease_expires_at > NOW()""",
                    (cleanup_id, lease_owner),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("artifact_cleanup_lease_lost")
                storage_type = str(row["storage_type"])
                storage_ref = str(row["storage_ref"])
                storage_scope = str(row.get("storage_scope") or "")
                task_id = str(row.get("task_id") or "").strip()
                if not task_id:
                    raise RuntimeError("artifact_cleanup_task_missing")
                if storage_type == "external" and not storage_scope:
                    cur.execute(
                        """UPDATE openair_artifact_cleanup_outbox
                              SET state = 'failed', last_error = 'legacy_external_bucket_unknown',
                                  lease_owner = NULL, lease_expires_at = NULL,
                                  terminal_at = NOW(), updated_at = NOW()
                            WHERE cleanup_id = %s AND state = 'claimed' AND lease_owner = %s""",
                        (cleanup_id, lease_owner),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError("artifact_cleanup_lease_lost")
                    return "preserved"
                cur.execute(
                    "SELECT pg_advisory_lock(openair_artifact_storage_lock_key(%s, %s, %s))",
                    (storage_type, storage_scope, storage_ref),
                )
                cur.execute(
                    """SELECT 1 FROM openair_artifact_storage_references
                         WHERE storage_type = %s AND storage_scope = %s
                           AND storage_ref = %s
                         LIMIT 1""",
                    (storage_type, storage_scope, storage_ref),
                )
                if cur.fetchone() is not None:
                    cur.execute(
                        """UPDATE openair_artifact_cleanup_outbox
                              SET state = 'preserved', last_error = NULL,
                                  lease_owner = NULL, lease_expires_at = NULL,
                                  terminal_at = NOW(), updated_at = NOW()
                            WHERE cleanup_id = %s AND state = 'claimed'
                              AND lease_owner = %s""",
                        (cleanup_id, lease_owner),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError("artifact_cleanup_lease_lost")
                    return "preserved"
                delete_callback(task_id, storage_type, storage_scope, storage_ref)
                cur.execute(
                    """UPDATE openair_artifact_cleanup_outbox
                          SET state = 'completed', last_error = NULL,
                              lease_owner = NULL, lease_expires_at = NULL,
                              terminal_at = NOW(), updated_at = NOW()
                        WHERE cleanup_id = %s AND state = 'claimed'
                          AND lease_owner = %s""",
                    (cleanup_id, lease_owner),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("artifact_cleanup_lease_lost")
                return "deleted"

    def storage_reference_is_catalogued(
        self, *, storage_type: str, storage_ref: str, task_id: str | None = None,
        storage_scope: str | None = None
    ) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM openair_artifact_storage_references
                         WHERE storage_type = %s AND storage_scope = %s
                           AND storage_ref = %s
                         LIMIT 1""",
                    (storage_type, _storage_scope(storage_type, task_id, storage_scope), storage_ref),
                )
                return cur.fetchone() is not None

    def complete_storage_cleanup_claim(self, *, cleanup_id: str, lease_owner: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_artifact_cleanup_outbox
                          SET state = 'completed', last_error = NULL,
                              lease_owner = NULL, lease_expires_at = NULL,
                              terminal_at = NOW(), updated_at = NOW()
                        WHERE cleanup_id = %s AND state = 'claimed'
                          AND lease_owner = %s
                        RETURNING cleanup_id""",
                    (cleanup_id, lease_owner),
                )
                return cur.fetchone() is not None

    def fail_storage_cleanup_claim(
        self,
        *,
        cleanup_id: str,
        lease_owner: str,
        error: str,
        max_attempts: int = 8,
    ) -> str:
        """Back off retryable cleanup failures and terminalize poison rows."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT attempt_count FROM openair_artifact_cleanup_outbox
                         WHERE cleanup_id = %s AND state = 'claimed'
                           AND lease_owner = %s
                         FOR UPDATE""",
                    (cleanup_id, lease_owner),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("artifact_cleanup_lease_lost")
                attempts = int(row.get("attempt_count") or 0)
                terminal = attempts >= max_attempts
                delay_seconds = min(3600, 2 ** min(max(attempts, 1), 12))
                next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                state = "failed" if terminal else "pending"
                cur.execute(
                    """UPDATE openair_artifact_cleanup_outbox
                          SET state = %s, last_error = %s,
                              lease_owner = NULL, lease_expires_at = NULL,
                              next_attempt_at = %s,
                              terminal_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                              updated_at = NOW()
                        WHERE cleanup_id = %s AND state = 'claimed'
                          AND lease_owner = %s""",
                    (
                        state,
                        error[:1000],
                        next_attempt_at,
                        terminal,
                        cleanup_id,
                        lease_owner,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("artifact_cleanup_lease_lost")
                return state

    def list_by_scope(
        self,
        owner_id: str,
        workspace_id: str,
        *,
        kind: str | None = None,
        origin: str | None = None,
        source_task_id: str | None = None,
        related_stage: str | None = None,
        format: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
        search_inline: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """按 owner/workspace 列产物，精确过滤 + 大小写不敏感搜索 + 分页。返回 (rows, total)。

        ``sourceTaskTitle`` prefers the live ``openair_tasks.title`` so catalog
        rows stay correct even when denormalized ``source_task_title`` was null
        (e.g. orchestration inserts that omitted the column).
        """
        where = [
            "a.owner_id = %s",
            "a.workspace_id = %s",
            "COALESCE(a.metadata->>'deletedWithTask', 'false') <> 'true'",
            "(LOWER(COALESCE(a.metadata->>'artifactVisibility', a.metadata->>'visibility', a.metadata->>'audience', '')) NOT IN ('internal', 'diagnostic', 'audit', 'developer', 'hidden'))",
        ]
        params: list[Any] = [owner_id, workspace_id]
        if kind is not None:
            where.append("a.kind = %s")
            params.append(kind)
        if origin is not None:
            where.append("a.origin = %s")
            params.append(origin)
        if source_task_id is not None:
            where.append("a.source_task_id = %s")
            params.append(source_task_id)
        if related_stage is not None:
            where.append("a.related_stage = %s")
            params.append(related_stage)
        if format is not None:
            where.append("a.format = %s")
            params.append(format)
        if q:
            like = f"%{q}%"
            search_cols = ["a.title", "a.summary"]
            if search_inline:
                search_cols.append("a.storage_ref")
            where.append("(" + " OR ".join(f"{c} ILIKE %s" for c in search_cols) + ")")
            params.extend([like] * len(search_cols))

        where_sql = " AND ".join(where)
        offset = (max(page, 1) - 1) * max(page_size, 1)

        cols = (
            "a.artifact_id, a.owner_id, a.workspace_id, a.created_by, a.kind, a.origin, a.title, a.summary, "
            "a.format, a.storage_type, a.storage_scope, a.storage_ref, a.external_provider, a.external_uri, a.content_size, "
            "a.source_task_id, "
            "COALESCE(NULLIF(BTRIM(t.title), ''), a.source_task_title) AS source_task_title, "
            "a.source_capability_call_id, a.related_stage, "
            "a.related_skill_id, a.metadata, a.created_at, a.source_execution_run_id, a.source_mode"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS c FROM openair_artifacts AS a WHERE {where_sql}",
                    params,
                )
                total = int(cur.fetchone()["c"])  # type: ignore[index]
                cur.execute(
                    f"SELECT {cols} FROM openair_artifacts AS a "
                    "LEFT JOIN openair_tasks AS t "
                    "  ON t.task_id = a.source_task_id AND t.status <> 'deleted' "
                    f"WHERE {where_sql} "
                    "ORDER BY a.created_at DESC NULLS LAST, a.artifact_id ASC "
                    "LIMIT %s OFFSET %s",
                    [*params, page_size, offset],
                )
                rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows], total

    def summary_visible_by_tasks(
        self,
        owner_id: str,
        workspace_id: str,
        task_ids: list[str],
        *,
        item_limit: int = 4,
    ) -> dict[str, dict[str, Any]]:
        """Batched visible-artifact summary per source_task_id: ``{count, items}``.

        ``items`` is the top-``item_limit`` artifacts (newest first) as
        ``{artifactId, title}`` — enough for the task-board icon links; ``count``
        is the accurate total (for the "+N" overflow). Applies the same
        visibility filters as ``list_by_scope`` so the board matches the
        artifact library. Returns only task_ids with >= 1 visible artifact.
        """
        if not task_ids:
            return {}
        sql = """
            WITH ranked AS (
                SELECT source_task_id,
                       artifact_id,
                       title,
                       ROW_NUMBER() OVER (
                           PARTITION BY source_task_id
                           ORDER BY created_at DESC NULLS LAST, artifact_id ASC
                       ) AS rn,
                       COUNT(*) OVER (PARTITION BY source_task_id) AS cnt
                  FROM openair_artifacts
                 WHERE owner_id = %s
                   AND workspace_id = %s
                   AND source_task_id = ANY(%s)
                   AND COALESCE(metadata->>'deletedWithTask', 'false') <> 'true'
                   AND LOWER(COALESCE(metadata->>'artifactVisibility',
                                      metadata->>'visibility',
                                      metadata->>'audience', ''))
                       NOT IN ('internal', 'diagnostic', 'audit', 'developer', 'hidden')
            )
            SELECT source_task_id, artifact_id, title, cnt
              FROM ranked
             WHERE rn <= %s
        """
        out: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (owner_id, workspace_id, list(task_ids), item_limit))
                for row in cur.fetchall():
                    tid = str(row["source_task_id"])
                    entry = out.setdefault(tid, {"count": int(row["cnt"]), "items": []})
                    entry["count"] = int(row["cnt"])
                    entry["items"].append({"artifactId": row["artifact_id"], "title": row["title"]})
        return out


    @staticmethod
    def _task_payload(row: dict[str, Any]) -> dict[str, Any]:
        value = row.get("payload")
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            return json.loads(value)
        raise ValueError("task payload is unavailable")

    @staticmethod
    def _receipt_fingerprint(*values: object) -> str:
        import hashlib
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _find_receipt(
        payload: dict[str, Any], *, action: str, idempotency_key: str | None, fingerprint: str
    ) -> dict[str, Any] | None:
        key = (idempotency_key or "").strip()
        if not key:
            return None
        for receipt in payload.get("userCommandReceipts") or []:
            if not isinstance(receipt, dict):
                continue
            if receipt.get("action") == action and receipt.get("idempotencyKey") == key:
                if receipt.get("fingerprint") != fingerprint:
                    raise RuntimeError("idempotency_payload_conflict")
                return receipt
        return None

    @staticmethod
    def _append_receipt(
        payload: dict[str, Any], *, action: str, idempotency_key: str | None,
        fingerprint: str, revision: int, artifact_id: str
    ) -> None:
        key = (idempotency_key or "").strip()
        if not key:
            return
        receipts = list(payload.get("userCommandReceipts") or [])
        receipts.append({
            "action": action,
            "idempotencyKey": key,
            "fingerprint": fingerprint,
            "status": "committed",
            "artifactId": artifact_id,
            "committedRevision": revision,
            "committedAt": datetime.now(timezone.utc).isoformat(),
        })
        payload["userCommandReceipts"] = receipts[-50:]

    @staticmethod
    def _update_legacy_embedded_artifact(
        payload: dict[str, Any],
        artifact_id: str,
        changes: dict[str, Any] | None,
        *,
        include_research_projection: bool = False,
    ) -> None:
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            return
        if changes is None:
            payload["artifacts"] = [
                row for row in artifacts
                if not isinstance(row, dict) or row.get("id") != artifact_id
            ]
            return
        for row in artifacts:
            if not isinstance(row, dict):
                continue
            is_research_projection = "artifactId" in row
            row_id = str(row.get("artifactId") if is_research_projection else row.get("id") or "")
            if row_id != artifact_id or (is_research_projection and not include_research_projection):
                continue
            if is_research_projection:
                # ResearchTaskV12.ArtifactV12 forbids unknown fields. Keep only
                # the revision-authority facts its contract owns; catalog storage
                # identity remains canonical in openair_artifacts.
                for key in ("artifactState", "version", "validatorResult"):
                    if key in changes:
                        row[key] = changes[key]
            else:
                row.update(changes)

    @staticmethod
    def _revision_validator_result(
        validator: Any,
        *,
        content_sha256: str,
        status: str,
        revision_source: str,
    ) -> dict[str, Any] | None:
        if not isinstance(validator, dict):
            return None
        contract = validator.get("artifactContract")
        schema_digest = str(validator.get("artifactSchemaDigest") or "").removeprefix("sha256:")
        if (
            not isinstance(contract, str)
            or not contract.strip()
            or len(schema_digest) != 64
            or any(ch not in "0123456789abcdefABCDEF" for ch in schema_digest)
        ):
            return None
        updated = dict(validator)
        updated["status"] = status
        updated["contentSha256"] = content_sha256
        updated["revisionSource"] = revision_source
        return updated

    @staticmethod
    def _current_version_source(metadata: dict[str, Any]) -> str | None:
        current_version_id = str(metadata.get("currentVersionId") or "").strip()
        versions = metadata.get("versions")
        if not current_version_id or not isinstance(versions, list):
            return None
        current = next(
            (
                row
                for row in versions
                if isinstance(row, dict)
                and str(row.get("versionId") or "") == current_version_id
            ),
            None,
        )
        return str(current.get("source") or "") if isinstance(current, dict) else None

    @staticmethod
    def _commit_task_revision(
        cur: Any,
        *,
        task_row: dict[str, Any],
        payload: dict[str, Any],
        lifecycle_fact: ArtifactLifecycleFact | None = None,
    ) -> int:
        payload["updatedAt"] = datetime.now(UTC).isoformat()
        try:
            changed = commit_task_lifecycle_mutation_locked(
                cur,
                task_row=task_row,
                payload=payload,
                task_id=str(task_row["task_id"]),
                owner_id=str(task_row["owner_id"]),
                workspace_id=str(task_row["workspace_id"]),
                fact=lifecycle_fact,
            )
        except TaskLifecycleWriteConflictError as exc:
            raise RuntimeError("task_revision_conflict") from exc
        if not changed:
            raise RuntimeError("task_lifecycle_transition_rejected")
        return int(task_row.get("revision") or 0) + 1

    def register_user_upload(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        artifact: dict[str, Any],
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        fingerprint = self._receipt_fingerprint(
            artifact.get("title"), artifact.get("contentSha256"), artifact.get("contentSize"), artifact.get("kind")
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT task_id, owner_id, workspace_id, status, title, payload, revision
                         FROM openair_tasks
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                        FOR UPDATE""",
                    (task_id, owner_id, workspace_id),
                )
                task_row = cur.fetchone()
                if task_row is None:
                    raise KeyError("task not found")
                payload = self._task_payload(task_row)
                prior = self._find_receipt(
                    payload, action="artifact_upload", idempotency_key=idempotency_key, fingerprint=fingerprint
                )
                if prior:
                    prior_artifact = str(prior.get("artifactId") or "")
                    cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s", (prior_artifact,))
                    row = cur.fetchone()
                    if row:
                        return _row_to_dict(row)
                    raise RuntimeError("idempotent_artifact_missing")
                current_revision = int(task_row.get("revision") or 0)
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                self.insert_artifact_with_cursor(cur, task_id, artifact, task_row.get("title"))
                next_revision = current_revision + 1
                self._append_receipt(
                    payload, action="artifact_upload", idempotency_key=idempotency_key,
                    fingerprint=fingerprint, revision=next_revision, artifact_id=str(artifact["id"]),
                )
                self._commit_task_revision(cur, task_row=task_row, payload=payload)
        return dict(artifact)

    def prepare_user_upload(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        artifact_id: str,
        title: str,
        content_sha256: str,
        content_size: int,
        kind: str,
        storage_type: str,
        storage_ref: str | None,
        storage_scope: str | None,
        expected_revision: int | None,
        idempotency_key: str | None,
        cleanup_delay_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """Validate upload CAS and reserve cleanup before any blob side effect."""
        fingerprint = self._receipt_fingerprint(title, content_sha256, content_size, kind)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT task_id, owner_id, workspace_id, status, title, payload, revision
                         FROM openair_tasks
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                        FOR UPDATE""",
                    (task_id, owner_id, workspace_id),
                )
                task_row = cur.fetchone()
                if task_row is None:
                    raise KeyError("task not found")
                payload = self._task_payload(task_row)
                prior = self._find_receipt(
                    payload,
                    action="artifact_upload",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if prior:
                    prior_artifact = str(prior.get("artifactId") or "")
                    cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s", (prior_artifact,))
                    row = cur.fetchone()
                    if row:
                        return _row_to_dict(row)
                    raise RuntimeError("idempotent_artifact_missing")
                current_revision = int(task_row.get("revision") or 0)
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                if storage_type in {"external", "local"} and storage_ref:
                    self._enqueue_storage_cleanup_with_cursor(
                        cur,
                        task_id=task_id,
                        artifact_id=artifact_id,
                        storage_type=storage_type,
                        storage_ref=storage_ref,
                        storage_scope=storage_scope,
                        reason_code="upload_inflight_recovery",
                        not_before_seconds=cleanup_delay_seconds,
                    )
        return None

    @staticmethod
    def _ensure_historical_version_reference_with_cursor(
        cur: Any, *, artifact_id: str, source_task_id: str, version: dict[str, Any]
    ) -> None:
        """Publish the current immutable identity as historical before pointer advance.

        The storage-reference fence retires a replaced current identity.  Promoting
        the parent reference first guarantees cleanup sees a live historical owner
        after commit, including for pre-version-metadata artifacts.
        """
        version_id = str(version.get("versionId") or "").strip()
        storage_type = str(version.get("storageType") or "").strip().lower()
        digest = str(version.get("contentSha256") or "").strip().lower()
        size = version.get("contentSize")
        if (
            not version_id
            or storage_type not in {"inline", "external", "local", "object"}
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise RuntimeError("artifact_version_storage_reference_invalid")
        if storage_type == "object":
            # Shared content-addressed blobs are immutable by hash and must never
            # acquire cleanup ownership in openair_artifact_storage_references.
            return
        if storage_type == "local":
            storage_scope = source_task_id
            storage_ref = str(version.get("path") or "").strip()
        elif storage_type == "external":
            storage_scope = str(version.get("storageScope") or "").strip()
            storage_ref = str(version.get("externalKey") or "").strip()
        else:
            storage_scope = artifact_id
            storage_ref = digest
        if not storage_scope or not storage_ref:
            raise RuntimeError("artifact_version_storage_reference_invalid")

        cur.execute(
            """INSERT INTO openair_artifact_storage_references (
                   artifact_id, reference_kind, version_id, source_task_id,
                   storage_type, storage_scope, storage_ref, content_sha256, content_size, updated_at
               ) VALUES (%s, 'historical', %s, %s, %s, %s, %s, %s, %s, NOW())
               ON CONFLICT (artifact_id, reference_kind, version_id) DO NOTHING""",
            (artifact_id, version_id, source_task_id, storage_type, storage_scope, storage_ref, digest, size),
        )
        cur.execute(
            """SELECT source_task_id, storage_type, storage_scope, storage_ref,
                      content_sha256, content_size
                 FROM openair_artifact_storage_references
                WHERE artifact_id = %s AND reference_kind = 'historical' AND version_id = %s
                FOR UPDATE""",
            (artifact_id, version_id),
        )
        reference = cur.fetchone()
        expected = {
            "source_task_id": source_task_id,
            "storage_type": storage_type,
            "storage_scope": storage_scope,
            "storage_ref": storage_ref,
            "content_sha256": digest,
            "content_size": size,
        }
        if reference is None or any(reference.get(key) != value for key, value in expected.items()):
            raise RuntimeError("artifact_historical_version_immutable")

    def append_content_version(
        self,
        *,
        artifact_id: str,
        owner_id: str,
        workspace_id: str,
        created_by: str,
        version_id: str,
        source: Literal["user_edit", "restore"],
        storage_type: Literal["external", "local"],
        storage_ref: str,
        storage_scope: str | None,
        content_sha256: str,
        content_size: int,
        feedback: str | None = None,
        restored_from_version_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Atomically append a content version and advance the canonical Artifact row.

        Blob creation is intentionally performed before this transaction by the route.
        The row pointer only advances after that write succeeds; a failed DB commit leaves
        at most an orphan blob for compensating cleanup, never a dangling canonical pointer.
        """
        normalized_ref = str(storage_ref or "").strip()
        normalized_hash = str(content_sha256 or "").strip().lower()
        if source not in {"user_edit", "restore"}:
            raise ValueError("artifact_version_source_invalid")
        if source == "restore" and not restored_from_version_id:
            raise ValueError("artifact_restore_source_version_required")
        if source != "restore" and restored_from_version_id is not None:
            raise ValueError("artifact_restore_source_version_invalid")
        if storage_type not in {"external", "local"} or not normalized_ref:
            raise ValueError("artifact_version_storage_reference_invalid")
        if storage_type == "external" and not str(storage_scope or "").strip():
            raise ValueError("artifact_external_storage_scope_required")
        if len(normalized_hash) != 64 or any(ch not in "0123456789abcdef" for ch in normalized_hash):
            raise ValueError("artifact_version_hash_missing_or_invalid")
        if isinstance(content_size, bool) or not isinstance(content_size, int) or content_size < 0:
            raise ValueError("artifact_version_size_missing_or_invalid")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s FOR UPDATE", (artifact_id,))
                artifact_row = cur.fetchone()
                if artifact_row is None:
                    raise KeyError("artifact not found")
                if artifact_row.get("owner_id") != owner_id or artifact_row.get("workspace_id") != workspace_id:
                    raise PermissionError("artifact is outside request scope")
                task_id = str(artifact_row.get("source_task_id") or "").strip()
                if not task_id:
                    raise RuntimeError("artifact_source_task_missing")
                cur.execute(
                    """SELECT task_id, owner_id, workspace_id, status, title, payload, revision
                         FROM openair_tasks WHERE task_id = %s FOR UPDATE""",
                    (task_id,),
                )
                task_row = cur.fetchone()
                if task_row is None or task_row.get("owner_id") != owner_id or task_row.get("workspace_id") != workspace_id:
                    raise KeyError("task not found")
                current_revision = int(task_row.get("revision") or 0)
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")

                artifact = ensure_initial_version_truth(_row_to_dict(artifact_row))
                versions = [dict(v) for v in artifact.get("versions") or [] if isinstance(v, dict)]
                current_version_id = str(artifact.get("currentVersionId") or "").strip()
                if not versions or not current_version_id:
                    raise RuntimeError("artifact_initial_version_unavailable")
                parent_version = next(
                    (v for v in versions if str(v.get("versionId") or "") == current_version_id), None
                )
                if parent_version is None or parent_version.get("source") == "revision_requested":
                    raise RuntimeError("artifact_current_version_invalid")
                self._ensure_historical_version_reference_with_cursor(
                    cur, artifact_id=artifact_id, source_task_id=task_id, version=parent_version
                )
                if any(str(v.get("versionId") or "") == version_id for v in versions):
                    raise RuntimeError("artifact_version_id_conflict")
                if restored_from_version_id is not None:
                    target = next((v for v in versions if str(v.get("versionId") or "") == restored_from_version_id), None)
                    if target is None:
                        raise KeyError("version not found")
                    if target.get("source") == "revision_requested":
                        raise ValueError("cannot restore a metadata-only revision_requested event")
                    if (
                        str(target.get("contentSha256") or "").lower() != normalized_hash
                        or target.get("contentSize") != content_size
                    ):
                        raise ValueError("artifact_restore_content_mismatch")

                parent_version_id = current_version_id
                next_number = max((int(v.get("versionNumber") or 0) for v in versions), default=0) + 1
                now = datetime.now(timezone.utc).isoformat()
                version = {
                    "versionId": version_id,
                    "versionNumber": next_number,
                    "createdAt": now,
                    "createdBy": created_by,
                    "source": source,
                    "feedback": feedback,
                    "parentVersionId": parent_version_id,
                    "restoredFromVersionId": restored_from_version_id,
                    "storageType": storage_type,
                    "storageScope": storage_scope,
                    "inlineContent": None,
                    "path": normalized_ref if storage_type == "local" else None,
                    "externalKey": normalized_ref if storage_type == "external" else None,
                    "contentSha256": normalized_hash,
                    "contentSize": content_size,
                }
                versions.append(version)

                metadata = artifact_row.get("metadata") or {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                metadata = dict(metadata)
                prior_validator = metadata.get("validatorResult")
                metadata["versions"] = versions
                metadata["currentVersionId"] = version_id
                for key in ("reviewedAt", "reviewedBy", "reviewSource", "reviewNote"):
                    metadata.pop(key, None)
                prior_state = str(metadata.get("artifactState") or artifact.get("artifactState") or "")
                if prior_state in {"usable", "rejected_or_needs_revision"}:
                    metadata["artifactState"] = "needs_review"
                if prior_state == "rejected_or_needs_revision":
                    metadata["revisionRequestedAt"] = None
                    metadata["revisionFeedback"] = None
                revision_validator = self._revision_validator_result(
                    prior_validator,
                    content_sha256=normalized_hash,
                    status="revision_pending_review",
                    revision_source=source,
                )
                payload = self._task_payload(task_row)
                has_research_projection = any(
                    isinstance(row, dict) and str(row.get("artifactId") or "") == artifact_id
                    for row in payload.get("artifacts") or []
                )
                if has_research_projection and revision_validator is None:
                    raise RuntimeError("artifact_revision_validator_authority_missing")
                if revision_validator is not None:
                    metadata["validatorResult"] = revision_validator
                gate_projection = dict(artifact)
                gate_projection["artifactState"] = metadata.get("artifactState") or artifact.get("artifactState")
                apply_artifact_gate(gate_projection)
                metadata["humanGate"] = gate_projection.get("humanGate")

                cur.execute(
                    """UPDATE openair_artifacts
                          SET storage_type = %s, storage_scope = %s, storage_ref = %s,
                              content_sha256 = %s, content_size = %s, metadata = %s::jsonb,
                              updated_at = NOW()
                        WHERE artifact_id = %s""",
                    (storage_type, storage_scope, normalized_ref, normalized_hash, content_size,
                     json.dumps(metadata, ensure_ascii=False), artifact_id),
                )
                self._update_legacy_embedded_artifact(
                    payload,
                    artifact_id,
                    {
                        "storageType": storage_type,
                        "storageScope": storage_scope,
                        "path": normalized_ref if storage_type == "local" else None,
                        "externalKey": normalized_ref if storage_type == "external" else None,
                        "inlineContent": None,
                        "contentSha256": normalized_hash,
                        "contentSize": content_size,
                        "currentVersionId": version_id,
                        "versions": versions,
                        "artifactState": metadata.get("artifactState", prior_state or None),
                        "version": next_number,
                        "validatorResult": metadata.get("validatorResult"),
                        "revisionRequestedAt": metadata.get("revisionRequestedAt"),
                        "revisionFeedback": metadata.get("revisionFeedback"),
                    },
                    include_research_projection=True,
                )
                self._commit_task_revision(cur, task_row=task_row, payload=payload)
                cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,))
                result = cur.fetchone()
        return _row_to_dict(result)

    def review_artifact(
        self,
        *,
        artifact_id: str,
        owner_id: str,
        workspace_id: str,
        decision: str,
        reviewer: str,
        note: str | None,
        source: str,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        next_state = "usable" if decision == "approved" else "rejected_or_needs_revision"
        fingerprint = self._receipt_fingerprint(decision, reviewer, note, source)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s FOR UPDATE", (artifact_id,))
                artifact_row = cur.fetchone()
                if artifact_row is None:
                    raise KeyError("artifact not found")
                if artifact_row.get("owner_id") != owner_id or artifact_row.get("workspace_id") != workspace_id:
                    raise PermissionError("artifact is outside request scope")
                task_id = str(artifact_row.get("source_task_id") or "")
                cur.execute(
                    """SELECT task_id, owner_id, workspace_id, status, title, payload, revision
                         FROM openair_tasks WHERE task_id = %s FOR UPDATE""",
                    (task_id,),
                )
                task_row = cur.fetchone()
                if task_row is None or task_row.get("owner_id") != owner_id or task_row.get("workspace_id") != workspace_id:
                    raise KeyError("task not found")
                payload = self._task_payload(task_row)
                if self._find_receipt(payload, action="artifact_review", idempotency_key=idempotency_key, fingerprint=fingerprint):
                    return _row_to_dict(artifact_row)
                current_revision = int(task_row.get("revision") or 0)
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                artifact = _row_to_dict(artifact_row)
                if artifact.get("kind") == "research_start_draft" or artifact.get("artifactType") == "research_start_draft":
                    raise ValueError("research_start_draft artifacts are not reviewable research outputs")
                metadata = artifact_row.get("metadata") or {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                metadata = dict(metadata)
                prior_source = str(metadata.get("reviewSource") or "")
                if source == "user" and prior_source in {"system", "review_agent", "independent_model_call"}:
                    raise RuntimeError("canonical_review_precedence")
                apply_artifact_gate(artifact)
                gate = artifact.get("humanGate") or {}
                if next_state == "usable" and not gate.get("canConfirmPass"):
                    raise ValueError(f"artifact state {artifact.get('artifactState')} cannot be confirmed as usable")
                reviewed_at = datetime.now(timezone.utc).isoformat()
                current_version_source = self._current_version_source(metadata)
                revision_validator = None
                if current_version_source in {"user_edit", "restore"}:
                    current_hash = str(artifact_row.get("content_sha256") or "").strip().lower()
                    revision_validator = self._revision_validator_result(
                        metadata.get("validatorResult"),
                        content_sha256=current_hash,
                        status="revision_human_approved" if next_state == "usable" else "revision_rejected",
                        revision_source=current_version_source,
                    )
                    if revision_validator is None:
                        raise RuntimeError("artifact_revision_validator_authority_missing")
                metadata.update({
                    "artifactState": next_state,
                    "reviewedAt": reviewed_at,
                    "reviewedBy": reviewer,
                    "reviewSource": source,
                    "reviewNote": note,
                })
                if revision_validator is not None:
                    metadata["validatorResult"] = revision_validator
                gate_projection = dict(artifact)
                gate_projection["artifactState"] = next_state
                apply_artifact_gate(gate_projection)
                metadata["humanGate"] = gate_projection.get("humanGate")
                cur.execute(
                    "UPDATE openair_artifacts SET metadata = %s::jsonb, updated_at = NOW() WHERE artifact_id = %s",
                    (json.dumps(metadata, ensure_ascii=False), artifact_id),
                )
                self._update_legacy_embedded_artifact(payload, artifact_id, {
                    "artifactState": next_state,
                    "validatorResult": metadata.get("validatorResult"),
                    "reviewedAt": reviewed_at,
                    "reviewedBy": reviewer,
                    "reviewSource": source,
                    "reviewNote": note,
                }, include_research_projection=True)
                next_revision = current_revision + 1
                self._append_receipt(
                    payload, action="artifact_review", idempotency_key=idempotency_key,
                    fingerprint=fingerprint, revision=next_revision, artifact_id=artifact_id,
                )
                lifecycle_fact = None
                if next_state != "usable" and task_row.get("status") == "completed":
                    cur.execute(
                        """SELECT COUNT(*) AS count FROM openair_artifacts
                            WHERE source_task_id = %s
                              AND artifact_id <> %s
                              AND COALESCE(metadata->>'artifactState', '') = 'usable'
                              AND kind <> 'research_start_draft'""",
                        (task_id, artifact_id),
                    )
                    if int(cur.fetchone()["count"]) == 0:
                        lifecycle_fact = ArtifactLifecycleFact.ALL_RESEARCH_OUTPUTS_NO_LONGER_USABLE
                self._commit_task_revision(
                    cur,
                    task_row=task_row,
                    payload=payload,
                    lifecycle_fact=lifecycle_fact,
                )
                cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,))
                result = cur.fetchone()
        return _row_to_dict(result)

    def confirm_scope_expansion(
        self,
        *,
        artifact_id: str,
        owner_id: str,
        workspace_id: str,
        reviewer: str,
        note: str | None,
        source: str,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        fingerprint = self._receipt_fingerprint(reviewer, note, source)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s FOR UPDATE", (artifact_id,))
                artifact_row = cur.fetchone()
                if artifact_row is None:
                    raise KeyError("artifact not found")
                if artifact_row.get("owner_id") != owner_id or artifact_row.get("workspace_id") != workspace_id:
                    raise PermissionError("artifact is outside request scope")
                task_id = str(artifact_row.get("source_task_id") or "")
                cur.execute(
                    """SELECT task_id, owner_id, workspace_id, status, title, payload, revision
                         FROM openair_tasks WHERE task_id = %s FOR UPDATE""",
                    (task_id,),
                )
                task_row = cur.fetchone()
                if task_row is None or task_row.get("owner_id") != owner_id or task_row.get("workspace_id") != workspace_id:
                    raise KeyError("task not found")
                payload = self._task_payload(task_row)
                if self._find_receipt(payload, action="scope_confirmation", idempotency_key=idempotency_key, fingerprint=fingerprint):
                    return _row_to_dict(artifact_row)
                current_revision = int(task_row.get("revision") or 0)
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                artifact = _row_to_dict(artifact_row)
                apply_artifact_gate(artifact)
                gate = artifact.get("humanGate") or {}
                if not gate.get("requiresScopeExpansionConfirmation"):
                    raise ValueError(f"artifact state {artifact.get('artifactState')} does not require scope expansion confirmation")
                confirmed_at = datetime.now(timezone.utc).isoformat()
                metadata = artifact_row.get("metadata") or {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                changes = {
                    "scopeExpansionConfirmedAt": confirmed_at,
                    "scopeExpansionConfirmedBy": reviewer,
                    "scopeExpansionReviewSource": source,
                    "scopeExpansionNote": note,
                    "artifactState": "needs_input",
                    "missingInputs": ["expanded_scope_materials_or_execution"],
                }
                metadata.update(changes)
                cur.execute(
                    "UPDATE openair_artifacts SET metadata = %s::jsonb, updated_at = NOW() WHERE artifact_id = %s",
                    (json.dumps(metadata, ensure_ascii=False), artifact_id),
                )
                self._update_legacy_embedded_artifact(payload, artifact_id, changes)
                next_revision = current_revision + 1
                self._append_receipt(
                    payload, action="scope_confirmation", idempotency_key=idempotency_key,
                    fingerprint=fingerprint, revision=next_revision, artifact_id=artifact_id,
                )
                self._commit_task_revision(cur, task_row=task_row, payload=payload)
                cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,))
                result = cur.fetchone()
        return _row_to_dict(result)

    def delete_artifact_scoped(
        self,
        *,
        artifact_id: str,
        owner_id: str,
        workspace_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM openair_artifacts WHERE artifact_id = %s FOR UPDATE", (artifact_id,))
                artifact_row = cur.fetchone()
                if artifact_row is None:
                    cur.execute(
                        """SELECT payload FROM openair_user_delete_receipts
                             WHERE target_type = 'artifact' AND target_id = %s
                               AND owner_id = %s AND workspace_id = %s""",
                        (artifact_id, owner_id, workspace_id),
                    )
                    receipt = cur.fetchone()
                    if receipt is not None:
                        payload = receipt.get("payload") or {}
                        if isinstance(payload, str):
                            payload = json.loads(payload)
                        return dict(payload)
                    raise KeyError("artifact not found")
                if artifact_row.get("owner_id") != owner_id or artifact_row.get("workspace_id") != workspace_id:
                    raise PermissionError("artifact is outside request scope")
                task_id = str(artifact_row.get("source_task_id") or "")
                cur.execute(
                    """SELECT task_id, owner_id, workspace_id, status, title, payload, revision
                         FROM openair_tasks WHERE task_id = %s FOR UPDATE""",
                    (task_id,),
                )
                task_row = cur.fetchone()
                if task_row is None or task_row.get("owner_id") != owner_id or task_row.get("workspace_id") != workspace_id:
                    raise KeyError("task not found")
                current_revision = int(task_row.get("revision") or 0)
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                payload = self._task_payload(task_row)
                self._update_legacy_embedded_artifact(payload, artifact_id, None)
                cur.execute(
                    """SELECT storage_type, storage_scope, storage_ref, reference_kind, version_id
                         FROM openair_artifact_storage_references
                        WHERE artifact_id = %s AND storage_type IN ('external', 'local')
                        ORDER BY reference_kind, version_id
                        FOR UPDATE""",
                    (artifact_id,),
                )
                storage_references = [dict(row) for row in cur.fetchall()]
                for reference in storage_references:
                    self._enqueue_storage_cleanup_with_cursor(
                        cur,
                        task_id=task_id,
                        artifact_id=artifact_id,
                        storage_type=str(reference["storage_type"]),
                        storage_ref=str(reference["storage_ref"]),
                        storage_scope=str(reference.get("storage_scope") or ""),
                        reason_code="artifact_deleted_object_cleanup_pending",
                    )
                deleted_snapshot = _row_to_dict(artifact_row)
                cur.execute(
                    """INSERT INTO openair_user_delete_receipts
                           (target_type, target_id, owner_id, workspace_id, task_id, payload)
                       VALUES ('artifact', %s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (target_type, target_id, owner_id, workspace_id) DO NOTHING""",
                    (
                        artifact_id, owner_id, workspace_id, task_id,
                        json.dumps(deleted_snapshot, ensure_ascii=False),
                    ),
                )
                cur.execute("DELETE FROM openair_artifacts WHERE artifact_id = %s", (artifact_id,))
                lifecycle_fact = None
                if task_row.get("status") == "completed":
                    cur.execute(
                        """SELECT COUNT(*) AS count FROM openair_artifacts
                            WHERE source_task_id = %s
                              AND COALESCE(metadata->>'artifactState', '') = 'usable'
                              AND kind <> 'research_start_draft'""",
                        (task_id,),
                    )
                    if int(cur.fetchone()["count"]) == 0:
                        lifecycle_fact = ArtifactLifecycleFact.ALL_RESEARCH_OUTPUTS_NO_LONGER_USABLE
                self._commit_task_revision(
                    cur,
                    task_row=task_row,
                    payload=payload,
                    lifecycle_fact=lifecycle_fact,
                )
        return _row_to_dict(artifact_row)


    def get_storage_reference(
        self,
        *,
        artifact_id: str,
        reference_kind: Literal["current", "historical"],
        version_id: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_version_id = "" if reference_kind == "current" else str(version_id or "").strip()
        if reference_kind == "historical" and not normalized_version_id:
            raise ValueError("historical version_id is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT artifact_id, reference_kind, version_id, source_task_id,
                              storage_type, storage_scope, storage_ref,
                              content_sha256, content_size
                         FROM openair_artifact_storage_references
                        WHERE artifact_id = %s AND reference_kind = %s AND version_id = %s""",
                    (artifact_id, reference_kind, normalized_version_id),
                )
                row = cur.fetchone()
        return dict(row) if row is not None else None

    def list_storage_references(self, *, artifact_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT artifact_id, reference_kind, version_id, source_task_id,
                              storage_type, storage_scope, storage_ref,
                              content_sha256, content_size
                         FROM openair_artifact_storage_references
                        WHERE artifact_id = %s
                        ORDER BY reference_kind, version_id""",
                    (artifact_id,),
                )
                return [dict(row) for row in cur.fetchall()]

    def get_by_id(self, artifact_id: str) -> dict[str, Any] | None:
        cols = (
            "a.artifact_id, a.owner_id, a.workspace_id, a.created_by, a.kind, a.origin, a.title, a.summary, "
            "a.format, a.storage_type, a.storage_scope, a.storage_ref, a.external_provider, a.external_uri, a.content_size, "
            "a.content_sha256, a.source_task_id, "
            "COALESCE(NULLIF(BTRIM(t.title), ''), a.source_task_title) AS source_task_title, "
            "a.source_capability_call_id, "
            "a.related_stage, a.related_skill_id, a.metadata, a.created_at, a.archived_at, "
            "a.source_execution_run_id, a.source_mode"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {cols} FROM openair_artifacts AS a "
                    "LEFT JOIN openair_tasks AS t "
                    "  ON t.task_id = a.source_task_id AND t.status <> 'deleted' "
                    "WHERE a.artifact_id = %s "
                    "AND COALESCE(a.metadata->>'deletedWithTask', 'false') <> 'true'",
                    (artifact_id,),
                )
                row = cur.fetchone()
        return _row_to_dict(row) if row else None


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """snake 行 → camelCase 产物 dict（与 artifact_catalog._to_catalog_row 同形）。"""
    storage_type = row.get("storage_type")
    storage_ref = row.get("storage_ref")
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    validator_result = metadata.get("validatorResult") if isinstance(metadata.get("validatorResult"), dict) else {}
    raw_result_protocol = metadata.get("resultProtocol") or validator_result.get("resultProtocol")
    result_protocol = None
    if isinstance(raw_result_protocol, dict):
        try:
            result_protocol = normalize_result_protocol(raw_result_protocol)
        except ValueError:
            result_protocol = None
    artifact_state = normalize_artifact_state(
        metadata.get("artifactState") or validator_result.get("artifactState"),
        kind=row.get("kind"),
    )
    artifact: dict[str, Any] = {
        "id": row.get("artifact_id"),
        "taskId": metadata.get("taskId") or row.get("source_task_id"),
        "ownerId": row.get("owner_id"),
        "workspaceId": row.get("workspace_id"),
        "createdBy": row.get("created_by"),
        "kind": row.get("kind"),
        "origin": row.get("origin") or "generated",
        "title": row.get("title"),
        "summary": row.get("summary"),
        "createdAt": _iso(row.get("created_at")),
        "format": row.get("format"),
        "relatedStage": row.get("related_stage"),
        "stage": metadata.get("stage") or row.get("related_stage"),
        "relatedSkillId": row.get("related_skill_id"),
        "subStageInstanceId": metadata.get("subStageInstanceId"),
        "substage": metadata.get("substage"),
        "sourceCapabilityCallId": row.get("source_capability_call_id"),
        "sourceExecutionRunId": row.get("source_execution_run_id"),
        "sourceMode": row.get("source_mode") or metadata.get("sourceMode"),
        "artifactState": artifact_state,
        "validatorResult": metadata.get("validatorResult"),
        "resultProtocol": result_protocol,
        "artifactQuality": (
            artifact_quality_for_protocol(result_protocol)
            if result_protocol is not None
            else metadata.get("artifactQuality") or validator_result.get("artifactQuality")
        ),
        "warnings": (
            list(result_protocol.get("warnings") or [])
            if result_protocol is not None
            else metadata.get("warnings") or validator_result.get("warnings") or []
        ),
        "humanGate": metadata.get("humanGate"),
        "artifactType": metadata.get("artifactType") or row.get("kind"),
        "artifactVisibility": metadata.get("artifactVisibility"),
        "audience": metadata.get("audience"),
        "storageType": storage_type or "local",
        "storageScope": row.get("storage_scope"),
        "externalProvider": row.get("external_provider"),
        "externalUri": row.get("external_uri"),
        "contentSize": row.get("content_size"),
        "sourceTaskId": row.get("source_task_id"),
        "sourceTaskTitle": row.get("source_task_title"),
    }
    # storage_ref → 拆回 inlineContent / externalKey / path
    if storage_type == "inline":
        artifact["inlineContent"] = storage_ref
    elif storage_type == "external":
        artifact["externalKey"] = storage_ref
    else:
        artifact["path"] = storage_ref
    if "content_sha256" in row:
        artifact["contentSha256"] = row.get("content_sha256")
    if "archived_at" in row:
        artifact["archivedAt"] = _iso(row.get("archived_at"))
    evidence_refs = metadata.get("evidenceRefs")
    if "evidenceRefs" in metadata:
        artifact["evidenceRefs"] = evidence_refs
    linked_material_ids = metadata.get("linkedMaterialIds")
    if linked_material_ids:
        artifact["linkedMaterialIds"] = linked_material_ids
    material_manifest = metadata.get("materialManifest")
    if material_manifest:
        artifact["materialManifest"] = material_manifest
    content_manifest = metadata.get("contentManifest")
    if content_manifest:
        artifact["contentManifest"] = content_manifest
    memory_refs = metadata.get("memoryRefs")
    if memory_refs:
        artifact["memoryRefs"] = memory_refs
    trust_records = metadata.get("trustRecords")
    if trust_records:
        artifact["trustRecords"] = trust_records
    if metadata.get("sourceOutputPath"):
        artifact["sourceOutputPath"] = metadata.get("sourceOutputPath")
    # issue #302 versioning — round-trip through metadata so the artifact library
    # (catalog list/detail served from this repository) shows version status and
    # pending-revision state, not just the in-memory task store.
    if metadata.get("currentVersionId"):
        artifact["currentVersionId"] = metadata.get("currentVersionId")
    if metadata.get("versions"):
        artifact["versions"] = metadata.get("versions")
    if metadata.get("revisionRequestedAt"):
        artifact["revisionRequestedAt"] = metadata.get("revisionRequestedAt")
    if metadata.get("revisionFeedback") is not None:
        artifact["revisionFeedback"] = metadata.get("revisionFeedback")
    for key in (
        "structuredResearchStart",
        "researchStartConfirmation",
        "missingInputs",
        "nonCitableBoundaries",
        "downstreamInput",
    ):
        if key in metadata:
            artifact[key] = metadata[key]
    # Re-derive validatorResult / humanGate from the round-tripped state so the
    # catalog read path matches the in-memory store's gate (dev artifact-gate model).
    apply_artifact_gate(artifact)
    return artifact

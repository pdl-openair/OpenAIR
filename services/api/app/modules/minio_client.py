"""MinIO object storage client for artifact persistence.

Provides a thin wrapper around the ``minio`` SDK.  All functions are no-ops
when ``OPENAIR_ARTIFACT_STORAGE != "minio"``.
"""

from __future__ import annotations

import io
import json
import logging
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from minio import Minio
from minio.error import S3Error

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------

_client: Minio | None = None


def get_minio_client() -> Minio:
    """Return a lazily-initialised MinIO client singleton."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region="us-east-1",  # MinIO default; pinning avoids a per-call bucket-location lookup
        )
    return _client


def is_minio_enabled() -> bool:
    return settings.artifact_storage == "minio"


def _normalize_minio_endpoint(endpoint: str) -> str:
    value = str(endpoint or "").strip().rstrip("/")
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlsplit(value)
        value = parsed.netloc
    if not value or "/" in value:
        raise ValueError("artifact_external_storage_scope_invalid")
    return value


def current_minio_storage_scope() -> str:
    """Durable physical-object scope persisted with every new external Artifact."""
    endpoint = _normalize_minio_endpoint(settings.minio_endpoint)
    bucket = str(settings.minio_bucket or "").strip()
    if not bucket or "/" in bucket:
        raise ValueError("artifact_external_storage_scope_invalid")
    scheme = "minio+https" if settings.minio_secure else "minio+http"
    return f"{scheme}://{endpoint}/{bucket}"


def parse_minio_storage_scope(storage_scope: str) -> tuple[str, str, bool]:
    """Return (endpoint, bucket, secure) from a persisted scope; never guess."""
    raw = str(storage_scope or "").strip()
    if not raw:
        raise ValueError("artifact_external_storage_scope_missing")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"minio+http", "minio+https"} or not parsed.netloc:
        raise ValueError("artifact_external_storage_scope_invalid")
    bucket = parsed.path.lstrip("/")
    if not bucket or "/" in bucket or parsed.query or parsed.fragment:
        raise ValueError("artifact_external_storage_scope_invalid")
    return _normalize_minio_endpoint(parsed.netloc), bucket, parsed.scheme == "minio+https"


def _client_and_bucket_for_scope(storage_scope: str | None) -> tuple[Minio, str]:
    if storage_scope is None:
        # Compatibility for non-authoritative JSON/demo and immediate write verification.
        return get_minio_client(), str(settings.minio_bucket)
    endpoint, bucket, secure = parse_minio_storage_scope(storage_scope)
    endpoint = _scope_transport_endpoint(storage_scope, endpoint)
    if (
        storage_scope == current_minio_storage_scope()
        and endpoint == _normalize_minio_endpoint(settings.minio_endpoint)
    ):
        return get_minio_client(), bucket
    client = Minio(
        endpoint=endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=secure,
        region="us-east-1",
    )
    return client, bucket


def _scope_transport_endpoint(storage_scope: str, endpoint: str) -> str:
    """Resolve only an explicitly configured address for this persisted scope.

    Docker and host APIs can reach one MinIO instance at different addresses.
    Never reinterpret the persisted bucket, TLS scheme or object identity, and
    never fall back to the current store when a historical store is unavailable.
    """
    raw = settings.minio_scope_endpoint_overrides
    if not raw:
        return endpoint
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("artifact_storage_scope_endpoint_overrides_invalid") from exc
    if not isinstance(overrides, dict):
        raise ValueError("artifact_storage_scope_endpoint_overrides_invalid")
    for scope, target in overrides.items():
        if not isinstance(scope, str) or not isinstance(target, str):
            raise ValueError("artifact_storage_scope_endpoint_overrides_invalid")
        parse_minio_storage_scope(scope)
        if target != _normalize_minio_endpoint(target) or any(c in target for c in "@?#"):
            raise ValueError("artifact_storage_scope_endpoint_overrides_invalid")
    return overrides.get(storage_scope, endpoint)


# ---------------------------------------------------------------------------
# Bucket management
# ---------------------------------------------------------------------------


def ensure_minio_bucket() -> None:
    """Create the configured bucket if it doesn't exist."""
    client = get_minio_client()
    bucket = settings.minio_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("created MinIO bucket: %s", bucket)
    else:
        logger.info("MinIO bucket already exists: %s", bucket)


# ---------------------------------------------------------------------------
# Presigned URLs (short-lived, for direct browser/worker upload & download)
# ---------------------------------------------------------------------------


_presign_client: Minio | None = None


def _get_presign_client() -> Minio:
    """Minio client configured with the worker/browser-facing endpoint.

    presigned URLs embed the endpoint hostname; the API-internal ``minio_endpoint``
    (e.g. ``minio:9000`` inside docker) is not resolvable from a browser/worker, so
    we sign with ``minio_worker_endpoint`` when set.
    """
    global _presign_client  # noqa: PLW0603
    if _presign_client is None:
        endpoint = (settings.minio_worker_endpoint or settings.minio_endpoint).strip()
        _presign_client = Minio(
            endpoint=endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            # Pin region so the SDK signs WITHOUT connecting (a worker-facing endpoint
            # like localhost:9000 is not reachable from inside the API container). The
            # signature still validates because the browser hits that same host.
            region="us-east-1",
        )
    return _presign_client


def _client_for_presign() -> Minio:
    """Client used to sign presigned URLs.

    When a worker/browser-facing endpoint is configured, sign with that dedicated
    client; otherwise reuse the shared ``get_minio_client()`` (so the same mock in
    tests applies).
    """
    if settings.minio_worker_endpoint:
        return _get_presign_client()
    return get_minio_client()


def presigned_put_object(
    object_key: str, expires_seconds: int | None = None, *, storage_scope: str | None = None
) -> str:
    """Return a short-lived PUT URL bound to the durable bucket identity."""
    ttl = expires_seconds or settings.artifact_presign_ttl_seconds
    if storage_scope is None:
        client, bucket = _client_for_presign(), str(settings.minio_bucket)
    else:
        client, bucket = _client_and_bucket_for_scope(storage_scope)
    url = client.presigned_put_object(bucket, object_key, expires=timedelta(seconds=ttl))
    return str(url)


def presigned_get_object(
    object_key: str, expires_seconds: int | None = None, *, storage_scope: str | None = None
) -> str:
    """Return a short-lived GET URL; authoritative callers pass persisted scope."""
    ttl = expires_seconds or settings.artifact_presign_ttl_seconds
    if storage_scope is None:
        client, bucket = _client_for_presign(), str(settings.minio_bucket)
    else:
        client, bucket = _client_and_bucket_for_scope(storage_scope)
    url = client.presigned_get_object(bucket, object_key, expires=timedelta(seconds=ttl))
    return str(url)


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------


def _object_key(task_id: str, filename: str, *, object_key: str | None) -> str:
    """Build an immutable, collision-free object key.

    Artifacts are immutable: the same ``filename`` uploaded twice must NOT land on the
    same key (else the second upload silently overwrites the first artifact's bytes).
    The key is therefore ``{task_id}/{uuid}/{filename}`` — task-scoped, per-upload
    unique. ``object_key`` lets a caller pass a server-controlled key directly (e.g. the
    worker presigned-upload channel, where the key is signed into an upload session).
    """
    if object_key:
        return object_key
    return f"{task_id}/{uuid4().hex}/{filename}"


def upload_file(
    task_id: str,
    file_path: Path,
    content_type: str = "application/octet-stream",
    *,
    object_key: str | None = None,
) -> str:
    """Upload a local file to MinIO.  Returns the object key."""
    client = get_minio_client()
    object_key = _object_key(task_id, file_path.name, object_key=object_key)
    client.fput_object(
        settings.minio_bucket,
        object_key,
        str(file_path),
        content_type=content_type,
    )
    logger.debug("uploaded %s → %s", file_path, object_key)
    return object_key


def upload_bytes(
    task_id: str,
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    *,
    object_key: str | None = None,
) -> str:
    """Upload raw bytes to MinIO.  Returns the object key."""
    client = get_minio_client()
    object_key = _object_key(task_id, filename, object_key=object_key)
    client.put_object(
        settings.minio_bucket,
        object_key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    logger.debug("uploaded bytes → %s (%d bytes)", object_key, len(data))
    return object_key


def remove_object(object_key: str, *, storage_scope: str | None = None) -> None:
    """Delete an object from its persisted bucket; missing objects are idempotent."""
    client, bucket = _client_and_bucket_for_scope(storage_scope)
    try:
        client.remove_object(bucket, object_key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject"}:
            return
        raise


def stat_object(object_key: str, *, storage_scope: str | None = None) -> dict[str, Any] | None:
    """Return object metadata from its persisted bucket, or None if missing."""
    client, bucket = _client_and_bucket_for_scope(storage_scope)
    try:
        obj = client.stat_object(bucket, object_key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject"}:
            return None
        raise
    return {
        "key": obj.object_name,
        "bucket": bucket,
        "size": obj.size,
        "content_type": obj.content_type,
        "etag": obj.etag,
        "last_modified": obj.last_modified,
    }


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def download_bytes(object_key: str, *, storage_scope: str | None = None) -> bytes:
    """Download bytes from the persisted bucket when a scope is provided."""
    client, bucket = _client_and_bucket_for_scope(storage_scope)
    response = client.get_object(bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def download_to_file(
    object_key: str, dest_path: Path, *, storage_scope: str | None = None
) -> Path:
    """Download an object to a local file from its persisted bucket."""
    client, bucket = _client_and_bucket_for_scope(storage_scope)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    client.fget_object(bucket, object_key, str(dest_path))
    return dest_path


# ---------------------------------------------------------------------------
# List / existence helpers
# ---------------------------------------------------------------------------


def list_objects(prefix: str) -> list[dict[str, Any]]:
    """List objects under a prefix.  Returns list of dicts with key, size, etc."""
    client = get_minio_client()
    objects = client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True)
    result: list[dict[str, Any]] = []
    for obj in objects:
        result.append({
            "key": obj.object_name,
            "size": obj.size,
            "content_type": obj.content_type,
            "last_modified": obj.last_modified,
            "is_dir": obj.is_dir,
        })
    return result


def object_exists(object_key: str, *, storage_scope: str | None = None) -> bool:
    """Check if an object exists in the persisted bucket."""
    client, bucket = _client_and_bucket_for_scope(storage_scope)
    try:
        client.stat_object(bucket, object_key)
        return True
    except S3Error as e:
        if e.code == "NoSuchKey":
            return False
        raise


def download_to_tempfile(
    object_key: str, suffix: str = "", *, storage_scope: str | None = None
) -> Path:
    """Download an object to a temporary file. Caller cleans it up."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    return download_to_file(object_key, Path(tmp.name), storage_scope=storage_scope)

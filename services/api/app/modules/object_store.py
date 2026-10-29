"""Content-addressed object store for artifact content.

Content is keyed by ``sha256(content)``. This decouples artifact *content
identity* from any one task's lifecycle, which is the foundation for
multi-granularity reuse: two artifacts (in different projects) that
reference the same content share a single blob, and removing one
artifact does not orphan the other once the catalog refcounts the blob.

Properties:
  - dedup: identical bytes are stored once (put is idempotent);
  - integrity: the key is the checksum, so a read can verify what it got;
  - task-independent: the blob survives source-task deletion.

Backends (selected by ``OPENAIR_ARTIFACT_STORAGE``):
  - ``minio``: object key ``content/{sha256[:2]}/{sha256[2:]}`` in the
    configured artifact bucket, reusing :mod:`app.modules.minio_client`.
  - ``local``: sharded file under ``{workspace_root}/_content`` for dev
    without MinIO.

This is the content primitive only. Existing inline/local/external
artifact write paths are migrated onto it in subsequent steps; nothing
here changes existing behaviour yet.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
from pathlib import Path

from app.config import settings
from app.execution.execution_context import get_workspace_root

logger = logging.getLogger(__name__)

_HEX = "0123456789abcdef"
_CONTENT_PREFIX = "content"


def _is_valid_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and all(ch in _HEX for ch in value.lower())
        and value == value.lower()
    )


def _content_relpath(sha256: str) -> Path:
    """Sharded relative path for a content hash (keeps dirs small on local fs)."""
    return Path(sha256[:2]) / sha256[2:]


def _local_content_root() -> Path:
    return get_workspace_root() / "_content"


def _local_content_path(sha256: str) -> Path:
    return _local_content_root() / _content_relpath(sha256)


def _minio_content_key(sha256: str) -> str:
    return f"{_CONTENT_PREFIX}/{_content_relpath(sha256).as_posix()}"


def _backend() -> str:
    return (settings.artifact_storage or "local").strip().lower()


def put_content(
    data: bytes, *, content_type: str = "application/octet-stream"
) -> str:
    """Store ``data`` content-addressed; return its sha256 hex key.

    Idempotent: storing the same bytes twice writes the blob once.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    backend = _backend()
    if backend == "minio":
        _minio_put(sha256, data, content_type)
    else:
        _local_put(sha256, data)
    return sha256


def _minio_put(sha256: str, data: bytes, content_type: str) -> None:
    from app.modules import minio_client

    key = _minio_content_key(sha256)
    if minio_client.object_exists(key):
        return  # dedup: same content already stored
    client = minio_client.get_minio_client()
    client.put_object(
        settings.minio_bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    logger.debug("content-store put %s (%d bytes)", sha256, len(data))


def _local_put(sha256: str, data: bytes) -> None:
    path = _local_content_path(sha256)
    if path.exists():
        return  # dedup
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic publish: write temp then rename, so a partial write is never
    # mistaken for a valid content-addressed blob.
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def get_content(sha256: str) -> bytes:
    """Return the bytes for ``sha256``. Caller must have verified the key."""
    if not _is_valid_sha256(sha256):
        raise ValueError("content_hash_invalid")
    if _backend() == "minio":
        from app.modules import minio_client

        return minio_client.download_bytes(_minio_content_key(sha256))
    path = _local_content_path(sha256)
    if not path.exists():
        raise FileNotFoundError(f"content_not_found:{sha256}")
    return path.read_bytes()


def content_exists(sha256: str) -> bool:
    if not _is_valid_sha256(sha256):
        return False
    if _backend() == "minio":
        from app.modules import minio_client

        return minio_client.object_exists(_minio_content_key(sha256))
    return _local_content_path(sha256).exists()


def remove_content(sha256: str) -> None:
    """Best-effort delete of a content blob. Missing blobs are not an error.

    NOTE: safe removal requires catalog-level refcounting (a blob may be
    referenced by several artifacts). Callers must ensure no live
    references remain before removing; refcounting is handled at the
    catalog layer, not here.
    """
    if not _is_valid_sha256(sha256):
        return
    try:
        if _backend() == "minio":
            from app.modules import minio_client

            minio_client.remove_object(_minio_content_key(sha256))
        else:
            path = _local_content_path(sha256)
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        return

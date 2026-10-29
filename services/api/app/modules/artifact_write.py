"""Artifact 写入服务（Artifact-02）：决定存储去向（inline / external-MinIO / local）。

把 agent_service 与用户上传路由共享的"存储决策"收敛到一处：小文本
（≤ ``OPENAIR_ARTIFACT_INLINE_MAX_BYTES``）保持 inline；较大内容在
``OPENAIR_ARTIFACT_STORAGE=minio`` 时上传 MinIO 返回 external key，否则回退 local。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from uuid import uuid4

from app.config import settings

logger = logging.getLogger(__name__)


class ArtifactStorageError(RuntimeError):
    """A safe storage-boundary failure with no provider response attached."""


@dataclass(frozen=True)
class StoredArtifactBytes:
    storage_type: str
    storage_ref: str
    storage_scope: str | None = None


_WINDOWS_RESERVED_PATH_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _portable_local_segment(value: str) -> str:
    """Map a logical storage-ref segment to one portable filesystem segment."""
    encoded = quote(value, safe="-._~")
    if encoded.split(".", 1)[0].upper() in _WINDOWS_RESERVED_PATH_STEMS:
        encoded = "".join(f"%{byte:02X}" for byte in value.encode("utf-8"))
    while encoded.endswith("."):
        encoded = f"{encoded[:-1]}%2E"
    return encoded


def _durable_local_path(task_id: str, storage_ref: str) -> Path:
    """Resolve one canonical API-owned blob identity inside a task workspace."""
    raw_parts = storage_ref.split("/")
    if (
        not storage_ref
        or storage_ref != storage_ref.strip()
        or storage_ref.startswith("/")
        or storage_ref.endswith("/")
        or "\\" in storage_ref
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ArtifactStorageError("artifact_storage_reference_invalid")
    relative = PurePosixPath(storage_ref)
    if relative.is_absolute() or not relative.parts:
        raise ArtifactStorageError("artifact_storage_reference_invalid")

    from app.execution.execution_context import get_task_workspace_dir

    workspace = get_task_workspace_dir(task_id).resolve()
    target = (workspace / Path(*(_portable_local_segment(part) for part in relative.parts))).resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ArtifactStorageError("artifact_storage_reference_invalid") from exc
    return target


class ArtifactWriteService:
    """无状态服务：决定产物 blob 的存储方式。"""

    def decide_and_store(
        self,
        task_id: str,
        file_path: Path | None,
        inline_content: str | None,
    ) -> tuple[str, str | None, str | None]:
        """返回 (storage_type, inline_content_or_None, external_key_or_None)。

        - 小文本（≤ inline 阈值）→ ``inline``。
        - 否则，启用 MinIO 时 → 上传（文件，或超尺寸 inline 文本）返回 ``external`` + object key。
        - 否则 → ``local``（由调用方按工作区路径解析）。
        """
        threshold = settings.artifact_inline_max_bytes

        if inline_content is not None and len(inline_content.encode("utf-8")) <= threshold:
            return "inline", inline_content, None

        if settings.artifact_storage == "minio":
            try:
                from app.modules.minio_client import upload_bytes, upload_file

                if file_path and file_path.exists():
                    object_key = upload_file(task_id, file_path)
                    try:
                        file_path.unlink()
                    except OSError:
                        pass
                    return "external", None, object_key
                if inline_content is not None:  # 超尺寸 inline 文本
                    filename = f"inline-{task_id[:8]}-{hash(inline_content) % 100000:05d}.md"
                    object_key = upload_bytes(
                        task_id,
                        filename,
                        inline_content.encode("utf-8"),
                        content_type="text/markdown; charset=utf-8",
                    )
                    return "external", None, object_key
            except Exception as exc:  # noqa: BLE001
                logger.warning("MinIO upload failed, falling back to local: %s", exc)

        return "local", None, None

    def store_uploaded_bytes(
        self,
        task_id: str,
        filename: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> tuple[str, str | None, str | None, str | None]:
        """Store uploaded bytes; return (storage_type, inline_content, external_key, local_path).

        Used by the user-upload route: small text → inline; otherwise MinIO external
        (when enabled); otherwise written to the task workspace as a local file
        (``local_path`` is the workspace-relative path to record on the artifact).
        """
        threshold = settings.artifact_inline_max_bytes
        inline_content: str | None = None
        if len(data) <= threshold:
            try:
                inline_content = data.decode("utf-8")
            except UnicodeDecodeError:
                inline_content = None
        if inline_content is not None:
            return "inline", inline_content, None, None

        if settings.artifact_storage == "minio":
            try:
                from app.modules.minio_client import upload_bytes

                object_key = upload_bytes(task_id, filename, data, content_type=content_type)
                return "external", None, object_key, None
            except Exception as exc:  # noqa: BLE001
                logger.warning("MinIO upload failed, falling back to local: %s", exc)

        # local fallback: write into the task workspace
        from app.execution.execution_context import get_task_workspace_dir

        ws_dir = get_task_workspace_dir(task_id)
        ws_dir.mkdir(parents=True, exist_ok=True)
        # Immutable local path: uuid-prefixed so a same-name re-upload doesn't overwrite
        # a prior artifact's file (artifacts are immutable; see minio_client._object_key).
        local_name = f"{uuid4().hex}_{filename}"
        (ws_dir / local_name).write_bytes(data)
        return "local", None, None, local_name

    def store_durable_bytes(
        self,
        task_id: str,
        storage_ref: str,
        data: bytes,
        content_type: str,
        *,
        storage_scope: str | None = None,
    ) -> StoredArtifactBytes:
        """Persist immutable orchestration bytes using the configured backend.

        Unlike user uploads, this path never silently changes backend after a
        failure. The durable execution state machine must record a terminal
        storage failure while it still owns the lease.
        """
        if settings.artifact_storage == "minio":
            try:
                from app.modules.minio_client import current_minio_storage_scope, upload_bytes

                current_scope = current_minio_storage_scope()
                if storage_scope is not None and storage_scope != current_scope:
                    raise ArtifactStorageError("artifact_external_storage_scope_mismatch")
                uploaded_ref = upload_bytes(
                    task_id,
                    PurePosixPath(storage_ref).name,
                    data,
                    content_type=content_type,
                    object_key=storage_ref,
                )
            except ArtifactStorageError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ArtifactStorageError("artifact_storage_write_failed") from exc
            return StoredArtifactBytes(
                storage_type="external", storage_ref=uploaded_ref, storage_scope=current_scope
            )

        if settings.artifact_storage != "local":
            raise ArtifactStorageError("artifact_storage_backend_invalid")

        target = _durable_local_path(task_id, storage_ref)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.is_file() and target.read_bytes() == data:
                    return StoredArtifactBytes(storage_type="local", storage_ref=storage_ref, storage_scope=task_id)
                raise ArtifactStorageError("artifact_storage_result_conflict")
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(data)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        except ArtifactStorageError:
            raise
        except OSError as exc:
            raise ArtifactStorageError("artifact_storage_write_failed") from exc
        return StoredArtifactBytes(storage_type="local", storage_ref=storage_ref, storage_scope=task_id)

    def read_durable_bytes(
        self,
        task_id: str,
        storage_type: str,
        storage_ref: str,
        storage_scope: str | None = None,
    ) -> bytes | None:
        """Read back bytes for the persistence-time checksum barrier."""
        try:
            if storage_type == "external":
                from app.modules.minio_client import download_bytes, stat_object

                if not storage_scope:
                    raise ArtifactStorageError("artifact_external_storage_scope_missing")
                return (
                    download_bytes(storage_ref, storage_scope=storage_scope)
                    if stat_object(storage_ref, storage_scope=storage_scope)
                    else None
                )
            if storage_type == "local":
                target = _durable_local_path(task_id, storage_ref)
                return target.read_bytes() if target.is_file() else None
        except ArtifactStorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArtifactStorageError("artifact_storage_read_failed") from exc
        raise ArtifactStorageError("artifact_storage_backend_invalid")

    def delete_durable_bytes(
        self,
        task_id: str,
        storage_type: str,
        storage_ref: str,
        *,
        storage_scope: str | None = None,
    ) -> None:
        """Idempotently delete one API-owned durable blob."""
        try:
            if storage_type == "external":
                from app.modules.minio_client import remove_object

                if not storage_scope:
                    raise ArtifactStorageError("artifact_external_storage_scope_missing")
                remove_object(storage_ref, storage_scope=storage_scope)
                return
            if storage_type == "local":
                _durable_local_path(task_id, storage_ref).unlink(missing_ok=True)
                return
        except ArtifactStorageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ArtifactStorageError("artifact_storage_delete_failed") from exc
        raise ArtifactStorageError("artifact_storage_backend_invalid")


# 模块级单例（无状态）
artifact_write_service = ArtifactWriteService()

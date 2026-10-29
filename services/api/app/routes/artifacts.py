"""产物库路由（Artifact-01 读侧/复用 + Artifact-02 上传/presigned 下载）。

Artifact-scoped（非 task-scoped）入口：``/api/artifacts``。目录与详情从内存 ``store``
聚合；content/download 复用既有 ``artifact_content`` 解析器；reuse 在目标 task 下登记
``origin=imported`` 产物；upload 在目标 task 下登记 ``origin=user_upload`` 产物（Artifact-02，
存储经 ``ArtifactWriteService`` 走 inline/MinIO/local）；external blob 走 presigned GET 下载。
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from app.config import settings
from app.auth import (
    RequestIdentity,
    ensure_artifact_in_scope,
    ensure_task_in_scope,
    resolve_request_identity,
)
from app.models.schemas import (
    ArtifactContentOut,
    ArtifactDetailOut,
    ArtifactListOut,
    ArtifactOut,
    ArtifactRelationOut,
    ArtifactRelationsOut,
    ArtifactVersionOut,
    ArtifactVersionsOut,
    CatalogArtifactOut,
    ConfirmScopeExpansionIn,
    EditArtifactContentIn,
    IngestCallbackIn,
    ReassignArtifactIn,
    RegenerateArtifactIn,
    ReviewArtifactIn,
    ReuseArtifactIn,
    ReuseArtifactOut,
    UpdateArtifactMaterialsIn,
)
from app.modules.artifact_catalog import (
    find_artifact_by_id,
    list_catalog_artifacts,
    reuse_artifact as catalog_reuse_artifact,
)
from app.modules.artifact_content import (
    load_artifact_binary,
    load_artifact_content,
    resolve_artifact_path,
    validate_durable_bytes,
)
from app.modules.artifact_gate import validator_result_for_artifact
from app.modules.artifact_repository import ArtifactRepository
from app.modules.historical_research_record_projection import historical_record_display_projection
from app.modules.material_memory import compact_preview, infer_material_kind, is_material_upload, missing_fields_for_upload
from app.modules.artifact_write import ArtifactStorageError, artifact_write_service
from app.modules.task_repository import TaskRepository
from app.modules.task_store import store

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
_log = logging.getLogger(__name__)


def _read_artifact_context(
    artifact_id: str,
    identity: RequestIdentity,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve reads without mixing PostgreSQL artifacts with TaskStore."""
    if settings.persistence_backend == "postgres":
        artifact = ArtifactRepository().get_by_id(artifact_id)
        ensure_artifact_in_scope(artifact, identity)
        source_task_id = artifact.get("sourceTaskId") if artifact else None
        if not isinstance(source_task_id, str) or not source_task_id.strip():
            raise HTTPException(status_code=409, detail="该产物暂时无法读取。")
        return {
            "taskId": source_task_id,
            "title": artifact.get("sourceTaskTitle"),
        }, artifact

    task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert task is not None and artifact is not None
    return task, artifact


def _canonical_artifact_version(
    artifact: dict[str, Any], version_id: str
) -> dict[str, Any] | None:
    """Read one immutable version snapshot from the canonical artifact row."""
    versions = artifact.get("versions")
    if not isinstance(versions, list):
        return None
    matches = [
        row for row in versions
        if isinstance(row, dict) and str(row.get("versionId") or "") == version_id
    ]
    if len(matches) != 1:
        return None
    return dict(matches[0])


def _canonical_artifact_versions(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    versions = artifact.get("versions")
    return [dict(row) for row in versions if isinstance(row, dict)] if isinstance(versions, list) else []


def _artifact_with_current_storage_reference(artifact: dict[str, Any]) -> dict[str, Any]:
    """Bind a PostgreSQL current content read to its normalized reference row."""
    artifact_id = str(artifact.get("id") or "").strip()
    if not artifact_id:
        raise ValueError("artifact_storage_reference_missing")
    reference = ArtifactRepository().get_storage_reference(
        artifact_id=artifact_id, reference_kind="current"
    )
    candidate = dict(artifact)
    if reference is None:
        # Content-addressed (object) artifacts never have a row in
        # openair_artifact_storage_references — its CHECK only allows
        # inline/external/local, and the sync trigger skips object so shared
        # blobs are never auto-deleted. Fall back to the catalogued
        # storage_type/content_sha256, mirroring the task-scoped content route
        # (_read_task_scoped_artifact), so reused artifacts resolve on the
        # artifact-scoped content/download routes too.
        if str(candidate.get("storageType") or "") == "object" and candidate.get("contentSha256"):
            candidate.pop("externalKey", None)
            candidate.pop("path", None)
            return candidate
        raise ValueError("artifact_storage_reference_missing")
    storage_type = str(reference.get("storage_type") or "")
    storage_ref = str(reference.get("storage_ref") or "")
    candidate["storageType"] = storage_type
    candidate["storageScope"] = reference.get("storage_scope")
    candidate["contentSha256"] = reference.get("content_sha256")
    candidate["contentSize"] = reference.get("content_size")
    if storage_type == "external":
        candidate["externalKey"] = storage_ref
        candidate.pop("path", None)
    elif storage_type == "local":
        candidate["path"] = storage_ref
        candidate.pop("externalKey", None)
    elif storage_type == "inline":
        if str(candidate.get("contentSha256") or "").lower() != storage_ref.lower():
            raise ValueError("artifact_storage_reference_mismatch")
    else:
        raise ValueError("artifact_storage_reference_invalid")
    return candidate


def _version_with_storage_reference(
    *, artifact_id: str, version: dict[str, Any]
) -> dict[str, Any]:
    """Bind a historical content read to its normalized reference row."""
    version_id = str(version.get("versionId") or "").strip()
    if not version_id:
        raise ValueError("artifact_version_storage_reference_missing")
    candidate = dict(version)
    if str(candidate.get("storageType") or "").strip().lower() == "object":
        digest = str(candidate.get("contentSha256") or "").strip().lower()
        size = candidate.get("contentSize")
        if (
            len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise ValueError("artifact_version_storage_reference_invalid")
        candidate.pop("externalKey", None)
        candidate.pop("path", None)
        return candidate
    reference = ArtifactRepository().get_storage_reference(
        artifact_id=artifact_id, reference_kind="historical", version_id=version_id
    )
    if reference is None:
        raise ValueError("artifact_version_storage_reference_missing")
    storage_type = str(reference.get("storage_type") or "")
    storage_ref = str(reference.get("storage_ref") or "")
    candidate["storageType"] = storage_type
    candidate["storageScope"] = reference.get("storage_scope")
    candidate["contentSha256"] = reference.get("content_sha256")
    candidate["contentSize"] = reference.get("content_size")
    if storage_type == "external":
        candidate["externalKey"] = storage_ref
        candidate.pop("path", None)
    elif storage_type == "local":
        candidate["path"] = storage_ref
        candidate.pop("externalKey", None)
    elif storage_type == "inline":
        if str(candidate.get("contentSha256") or "").lower() != storage_ref.lower():
            raise ValueError("artifact_version_storage_reference_mismatch")
    else:
        raise ValueError("artifact_version_storage_reference_invalid")
    return candidate


def _read_canonical_version_bytes(
    *,
    task_id: str,
    artifact_id: str,
    version: dict[str, Any],
) -> bytes:
    """Read exactly the immutable bytes named by one PostgreSQL version row."""
    storage_type = str(version.get("storageType") or "")
    if storage_type == "inline":
        inline = version.get("inlineContent")
        if not isinstance(inline, str):
            raise FileNotFoundError("artifact version inline bytes missing")
        blob = inline.encode("utf-8")
    elif storage_type in {"local", "external"}:
        storage_ref = version.get("path") if storage_type == "local" else version.get("externalKey")
        if not isinstance(storage_ref, str) or not storage_ref.strip():
            raise ValueError("artifact_version_storage_reference_invalid")
        blob = artifact_write_service.read_durable_bytes(
            task_id, storage_type, storage_ref, version.get("storageScope")
        )
        if blob is None:
            raise FileNotFoundError("artifact version bytes missing")
    elif storage_type == "object":
        from app.modules import object_store

        digest = str(version.get("contentSha256") or "").strip().lower()
        blob = object_store.get_content(digest)
    else:
        raise ValueError("artifact_version_storage_reference_invalid")

    expected_hash = version.get("contentSha256")
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash.strip()) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in expected_hash.strip())
    ):
        raise ValueError("artifact_version_hash_missing_or_invalid")
    if hashlib.sha256(blob).hexdigest().lower() != expected_hash.strip().lower():
        raise ValueError("artifact_version_hash_mismatch")
    expected_size = version.get("contentSize")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
        raise ValueError("artifact_version_size_missing_or_invalid")
    if expected_size != len(blob):
        raise ValueError("artifact_version_size_mismatch")
    return blob


def _task_authority(task_id: str) -> dict[str, Any] | None:
    return TaskRepository().get_task(task_id) if settings.persistence_backend == "postgres" else store.get_task(task_id)


def _scoped_tasks(identity: RequestIdentity) -> list[dict[str, Any]]:
    source = TaskRepository().load_all_tasks() if settings.persistence_backend == "postgres" else store.list_tasks()
    return [
        task for task in source
        if task.get("ownerId") == identity["ownerId"] and task.get("workspaceId") == identity["workspaceId"]
    ]


def _cleanup_uploaded_storage(
    task_id: str,
    storage_type: str,
    external_key: str | None,
    local_path: str | None,
) -> str | None:
    """Compensate object/file creation; return an error for durable outboxing."""
    if storage_type == "external" and external_key:
        try:
            from app.modules.minio_client import remove_object
            remove_object(external_key)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
    elif storage_type == "local" and local_path:
        try:
            from pathlib import Path as LocalPath
            candidate = LocalPath(local_path)
            if not candidate.is_absolute():
                from app.execution.execution_context import get_task_workspace_dir
                candidate = get_task_workspace_dir(task_id) / candidate
            candidate.unlink()
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"
    return None


def _outbox_cleanup_failure(
    *,
    task_id: str,
    artifact_id: str | None,
    storage_type: str,
    external_key: str | None,
    local_path: str | None,
    reason_code: str,
    last_error: str,
) -> None:
    storage_ref = external_key if storage_type == "external" else local_path
    if storage_type not in {"external", "local"} or not storage_ref:
        return
    try:
        ArtifactRepository().enqueue_storage_cleanup(
            task_id=task_id,
            artifact_id=artifact_id,
            storage_type=storage_type,
            storage_ref=str(storage_ref),
            reason_code=reason_code,
            last_error=last_error,
        )
    except Exception:
        _log.exception("failed to persist artifact cleanup receipt ref=%s", storage_ref)


def _origin_source_mode(_artifact: dict[str, Any], resolved_mode: str) -> str:
    """Expose the actual content resolver mode, not persisted execution provenance."""
    return resolved_mode


def _external_download_is_authoritative(task_id: str, artifact: dict[str, Any]) -> bool:
    """Check object existence and checksum before issuing a durable download URL."""
    external_key = artifact.get("externalKey")
    if not isinstance(external_key, str) or not external_key.strip():
        return False
    try:
        blob = artifact_write_service.read_durable_bytes(
            task_id, "external", external_key, artifact.get("storageScope")
        )
    except Exception:
        return False
    if blob is None:
        return False
    try:
        validate_durable_bytes(artifact, blob)
    except ValueError:
        return False
    return True


def _artifact_detail(task: dict[str, Any], artifact: dict[str, Any] | None) -> ArtifactDetailOut:
    """Build metadata-only public detail; content requires the canonical content route."""
    assert artifact is not None  # caller guarantees scope-resolved artifact
    detail = dict(artifact)
    detail.pop("inlineContent", None)
    detail.setdefault("origin", "generated")
    detail["sourceTaskId"] = task["taskId"]
    detail["sourceTaskTitle"] = task.get("title")
    return ArtifactDetailOut.model_validate(detail)


_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "webp": "image/webp",
}

_MATERIAL_TASK_HINTS = (
    "material",
    "materials",
    "材料",
    "seed",
    "evidence",
    "证据",
    "sulfide",
    "electrolyte",
    "interface",
    "xps",
    "eis",
    "sem",
)
_LEGACY_OPE_HINTS = ("ope", "offline policy", "训练动态")


def _latest_scoped_task(identity: RequestIdentity) -> dict[str, Any] | None:
    tasks = _scoped_tasks(identity)
    if not tasks:
        return None
    return max(tasks, key=lambda task: task.get("updatedAt") or task.get("createdAt") or "")


def _task_text(task: dict[str, Any]) -> str:
    values = [
        task.get("title"),
        task.get("goalSummary"),
        task.get("domain"),
        task.get("skillPack"),
        task.get("researchType"),
    ]
    return " ".join(str(v) for v in values if v).lower()


def _task_timestamp(task: dict[str, Any]) -> str:
    return str(task.get("updatedAt") or task.get("createdAt") or "")


def _default_upload_task(
    identity: RequestIdentity,
    *,
    filename: str,
    kind: str,
    content_type: str,
) -> dict[str, Any] | None:
    tasks = _scoped_tasks(identity)
    if not tasks:
        return None
    if not is_material_upload(kind, filename, content_type):
        return max(tasks, key=_task_timestamp)

    scored: list[tuple[int, str, dict[str, Any]]] = []
    for task in tasks:
        text = _task_text(task)
        score = 0
        if any(hint in text for hint in _MATERIAL_TASK_HINTS):
            score += 4
        if task.get("status") in {"running", "awaiting_confirmation", "draft", "not_started"}:
            score += 1
        if any(hint in text for hint in _LEGACY_OPE_HINTS) and not any(hint in text for hint in _MATERIAL_TASK_HINTS):
            score -= 4
        scored.append((score, _task_timestamp(task), task))
    best = max(scored, key=lambda item: (item[0], item[1]))
    if best[0] > 0:
        return best[2]
    return max(tasks, key=_task_timestamp)


def _resolve_upload_target(
    *,
    target_task_id: str | None,
    current_task_id: str | None,
    filename: str,
    kind: str,
    content_type: str,
    identity: RequestIdentity,
) -> dict[str, Any]:
    resolved_id = (target_task_id or current_task_id or "").strip()
    if resolved_id:
        return ensure_task_in_scope(_task_authority(resolved_id), identity)
    default_task = _default_upload_task(identity, filename=filename, kind=kind, content_type=content_type)
    if default_task is None:
        raise HTTPException(status_code=400, detail="targetTaskId is required when no scoped task exists")
    return default_task


def _task_domain(task: dict[str, Any] | None) -> str | None:
    if not task:
        return None
    return task.get("domain") or task.get("skillPack") or task.get("researchType")


def _ownership_confirmation_detail(
    *,
    source_task: dict[str, Any] | None,
    target_task: dict[str, Any],
) -> dict[str, Any] | None:
    if source_task is None:
        return None
    source_id = source_task.get("taskId")
    target_id = target_task.get("taskId")
    source_domain = _task_domain(source_task)
    target_domain = _task_domain(target_task)
    if source_id == target_id and source_domain == target_domain:
        return None
    reason = "cross_task" if source_id != target_id else "cross_domain"
    if source_id != target_id and source_domain != target_domain:
        reason = "cross_task_and_domain"
    return {
        "code": "ownership_confirmation_required",
        "reason": reason,
        "sourceTaskId": source_id,
        "sourceTaskTitle": source_task.get("title"),
        "sourceDomain": source_domain,
        "targetTaskId": target_id,
        "targetTaskTitle": target_task.get("title"),
        "targetDomain": target_domain,
    }


def _decode_preview(data: bytes, content_type: str) -> str | None:
    if not (content_type.startswith("text/") or "markdown" in content_type or "json" in content_type):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


@router.get("", response_model=ArtifactListOut)
def list_artifacts(
    identity: RequestIdentity = Depends(resolve_request_identity),
    kind: str | None = Query(default=None),
    origin: str | None = Query(default=None),
    sourceTaskId: str | None = Query(default=None),
    relatedStage: str | None = Query(default=None),
    format: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    searchInline: bool = Query(default=False),
) -> ArtifactListOut:
    rows, total = list_catalog_artifacts(
        identity,
        kind=kind,
        origin=origin,
        source_task_id=sourceTaskId,
        related_stage=relatedStage,
        format_=format,
        q=q,
        page=page,
        page_size=pageSize,
        search_inline=searchInline,
    )
    return ArtifactListOut(
        artifacts=[CatalogArtifactOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        pageSize=pageSize,
    )


@router.get("/{artifact_id}", response_model=ArtifactDetailOut)
def get_artifact(
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactDetailOut:
    task, artifact = _read_artifact_context(artifact_id, identity)
    assert task is not None  # artifact found ⇒ enclosing task exists
    return _artifact_detail(task, artifact)


@router.get("/{artifact_id}/content", response_model=ArtifactContentOut)
def get_artifact_content(
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactContentOut:
    task, artifact = _read_artifact_context(artifact_id, identity)
    assert task is not None
    try:
        if settings.persistence_backend == "postgres":
            artifact = _artifact_with_current_storage_reference(artifact)
        content, source_mode = load_artifact_content(
            task["taskId"],
            artifact,
            allow_replay_fallback=settings.persistence_backend != "postgres",
        )
    except (FileNotFoundError, NotImplementedError, PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "artifact_content_unavailable", "artifactId": artifact_id},
        ) from exc
    def load_source(reference: str) -> str:
        source_task, source = _read_artifact_context(reference, identity)
        assert source_task is not None
        if settings.persistence_backend == "postgres":
            source = _artifact_with_current_storage_reference(source)
        return load_artifact_content(
            source_task["taskId"], source,
            allow_replay_fallback=settings.persistence_backend != "postgres",
        )[0]

    display_projection = None
    try:
        display_projection = historical_record_display_projection(
            task, artifact, content, load_source=load_source,
        )
    except (OSError, ValueError, RuntimeError, KeyError, HTTPException):
        _log.exception("historical_record_display_projection_unavailable artifact_id=%s", artifact_id)
    return ArtifactContentOut(
        artifactId=artifact_id,
        path=artifact.get("path") or "",
        format=artifact.get("format"),
        sourceMode=cast(Literal["live", "replay", "external", "inline"], _origin_source_mode(artifact, source_mode)),
        content=content,
        displayProjection=display_projection,
        evidenceRefs=artifact.get("evidenceRefs") or [],
        linkedMaterialIds=artifact.get("linkedMaterialIds") or [],
    )


@router.get("/{artifact_id}/download", response_model=None)
def download_artifact(
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> Response | dict[str, str]:
    task, artifact = _read_artifact_context(artifact_id, identity)
    assert task is not None
    if settings.persistence_backend == "postgres":
        try:
            artifact = _artifact_with_current_storage_reference(artifact)
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_content_unavailable", "artifactId": artifact_id},
            ) from exc
    fmt = (artifact.get("format") or "").lower()
    media_type = _MEDIA_TYPES.get(fmt, "application/octet-stream")
    if settings.persistence_backend == "postgres":
        try:
            blob, _source_mode = load_artifact_binary(
                task["taskId"], artifact, allow_replay_fallback=False
            )
        except (FileNotFoundError, PermissionError, ValueError, ArtifactStorageError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_content_unavailable", "artifactId": artifact_id},
            ) from exc
        raw_filename = str(
            artifact.get("path")
            or artifact.get("externalKey")
            or artifact.get("title")
            or artifact_id
        ).split("/")[-1]
        filename = "".join(
            character for character in raw_filename
            if character.isalnum() or character in {".", "_", "-"}
        )[:160] or "artifact"
        return Response(
            content=blob,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if artifact.get("storageType") == "external":
        external_key = artifact.get("externalKey")
        if not external_key:
            raise HTTPException(status_code=404, detail="artifact content unavailable")
        from app.modules.minio_client import presigned_get_object

        url = presigned_get_object(external_key, settings.artifact_presign_ttl_seconds)
        return {"url": url}
    try:
        path = resolve_artifact_path(
            task["taskId"], artifact, task.get("capabilityCalls") or [],
            allow_replay_fallback=True,
        )
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="artifact content unavailable") from exc
    if not path:
        raise HTTPException(status_code=404, detail="artifact file not found")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.post("/upload", response_model=ArtifactOut)
async def upload_artifact(
    file: UploadFile = File(...),
    targetTaskId: str | None = Form(None),
    currentTaskId: str | None = Form(None),
    sourceTaskId: str | None = Form(None),
    ownershipConfirmed: bool = Form(False),
    kind: str = Form("user_upload"),
    title: str | None = Form(None),
    expectedRevision: int | None = Form(None),
    idempotencyKey: str | None = Form(None),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactOut:
    data = await file.read()
    if len(data) > settings.artifact_upload_max_bytes:
        raise HTTPException(status_code=413, detail="file exceeds upload size limit")

    # Sanitize filename (basename only — no path traversal).
    raw_name = file.filename or "upload"
    filename = os.path.basename(raw_name.replace("\\", "/")) or "upload"
    content_type = file.content_type or "application/octet-stream"
    fmt = os.path.splitext(filename)[1].lstrip(".").lower() or None

    target_task = _resolve_upload_target(
        target_task_id=targetTaskId,
        current_task_id=currentTaskId,
        filename=filename,
        kind=kind,
        content_type=content_type,
        identity=identity,
    )
    target_task_id = str(target_task["taskId"])
    source_task_id = (sourceTaskId or currentTaskId or "").strip()
    source_task = ensure_task_in_scope(_task_authority(source_task_id), identity) if source_task_id else None
    confirmation = _ownership_confirmation_detail(source_task=source_task, target_task=target_task)
    if confirmation and not ownershipConfirmed:
        raise HTTPException(status_code=409, detail=confirmation)

    if settings.persistence_backend == "postgres":
        created_at = datetime.now(timezone.utc).isoformat()
        created_artifact_id = f"artifact_{uuid4().hex}"
        inline: str | None = None
        if len(data) <= settings.artifact_inline_max_bytes:
            try:
                inline = data.decode("utf-8")
            except UnicodeDecodeError:
                inline = None
        storage_scope: str | None = None
        if inline is not None:
            storage_type = "inline"
            storage_ref = None
        elif settings.artifact_storage == "minio":
            from app.modules.minio_client import current_minio_storage_scope

            storage_type = "external"
            storage_scope = current_minio_storage_scope()
            storage_ref = f"{target_task_id}/uploads/{created_artifact_id}/{filename}"
        else:
            storage_type = "local"
            storage_scope = target_task_id
            storage_ref = f"uploads/{created_artifact_id}/{filename}"
        content_sha256 = hashlib.sha256(data).hexdigest()
        repository = ArtifactRepository()
        legacy_storage_written = False
        try:
            prepare_upload = getattr(repository, "prepare_user_upload", None)
            replay = (
                prepare_upload(
                    task_id=target_task_id,
                    owner_id=identity["ownerId"],
                    workspace_id=identity["workspaceId"],
                    artifact_id=created_artifact_id,
                    title=title or filename,
                    content_sha256=content_sha256,
                    content_size=len(data),
                    kind=kind,
                    storage_type=storage_type,
                    storage_ref=storage_ref,
                    storage_scope=storage_scope,
                    expected_revision=expectedRevision,
                    idempotency_key=idempotencyKey,
                )
                if callable(prepare_upload)
                else None
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="target task not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if replay is not None:
            return ArtifactOut.model_validate(replay)

        if not callable(prepare_upload):
            storage_type, inline, external_key, local_path = artifact_write_service.store_uploaded_bytes(
                target_task_id,
                filename,
                data,
                content_type=content_type,
            )
            storage_ref = external_key if storage_type == "external" else local_path
            legacy_storage_written = True

        external_key = storage_ref if storage_type == "external" else None
        local_path = storage_ref if storage_type == "local" else None
        if storage_ref is not None and not legacy_storage_written:
            try:
                artifact_write_service.store_durable_bytes(
                    target_task_id,
                    storage_ref,
                    data,
                    content_type,
                    storage_scope=storage_scope,
                )
            except ArtifactStorageError as exc:
                raise HTTPException(status_code=503, detail="artifact storage unavailable") from exc
        artifact: dict[str, Any] = {
            "id": created_artifact_id,
            "taskId": target_task_id,
            "sourceTaskId": target_task_id,
            "ownerId": identity["ownerId"],
            "workspaceId": identity["workspaceId"],
            "createdBy": identity["createdBy"],
            "kind": kind,
            "origin": "user_upload",
            "title": title or filename,
            "createdAt": created_at,
            "format": fmt,
            "mimeType": content_type,
            "storageType": storage_type,
            "storageScope": storage_scope,
            "contentSize": len(data),
            "contentSha256": content_sha256,
            "artifactState": "needs_review",
            "sourceMode": "none",
        }
        if inline is not None:
            artifact["inlineContent"] = inline
        if external_key:
            artifact["externalKey"] = external_key
            artifact["externalProvider"] = "minio"
        if local_path:
            artifact["path"] = local_path
        def compensate(reason_code: str) -> None:
            if storage_ref is None:
                return
            try:
                if legacy_storage_written:
                    cleanup_error = _cleanup_uploaded_storage(
                        target_task_id,
                        storage_type,
                        external_key,
                        local_path,
                    )
                    if cleanup_error:
                        raise RuntimeError(cleanup_error)
                else:
                    artifact_write_service.delete_durable_bytes(
                        target_task_id,
                        storage_type,
                        storage_ref,
                        storage_scope=storage_scope,
                    )
                mark_completed = getattr(repository, "mark_storage_cleanup_completed", None)
                if callable(mark_completed):
                    mark_completed(
                        storage_type=storage_type,
                        storage_ref=storage_ref,
                        task_id=target_task_id,
                        storage_scope=storage_scope,
                    )
            except Exception as exc:
                enqueue_cleanup = getattr(repository, "enqueue_storage_cleanup", None)
                if callable(enqueue_cleanup):
                    enqueue_cleanup(
                        task_id=target_task_id,
                        artifact_id=created_artifact_id,
                        storage_type=storage_type,
                        storage_ref=storage_ref,
                        storage_scope=storage_scope,
                        reason_code=reason_code,
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    _log.exception("artifact cleanup receipt unavailable ref=%s", storage_ref)

        try:
            artifact = repository.register_user_upload(
                task_id=target_task_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                artifact=artifact,
                expected_revision=expectedRevision,
                idempotency_key=idempotencyKey,
            )
            if str(artifact.get("id") or "") != created_artifact_id:
                # Idempotent replay returned the prior committed artifact; the
                # just-written duplicate object is not part of that receipt.
                compensate("idempotent_upload_duplicate")
            elif storage_ref is not None:
                mark_preserved = getattr(repository, "mark_storage_cleanup_preserved", None)
                if callable(mark_preserved):
                    mark_preserved(
                        storage_type=storage_type,
                        storage_ref=storage_ref,
                        task_id=target_task_id,
                        storage_scope=storage_scope,
                    )
        except KeyError as exc:
            compensate("upload_catalog_task_missing")
            raise HTTPException(status_code=404, detail="target task not found") from exc
        except RuntimeError as exc:
            compensate("upload_catalog_rejected")
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception:
            compensate("upload_catalog_failed")
            raise
        return ArtifactOut.model_validate(artifact)

    storage_type, inline, external_key, local_path = artifact_write_service.store_uploaded_bytes(
        target_task_id, filename, data, content_type=content_type
    )

    extra: dict[str, object] = {"origin": "user_upload"}
    if external_key:
        extra["externalKey"] = external_key
    artifact = store.add_artifact(
        target_task_id,
        kind=kind,
        title=title or filename,
        format=fmt,
        path=local_path,
        storage_type=storage_type,
        inline_content=inline,
        extra_fields=extra,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="target task not found")
    preview_text = inline if inline is not None else _decode_preview(data, content_type)
    if is_material_upload(kind, filename, content_type):
        material_kind = infer_material_kind(
            filename=filename,
            kind=kind,
            content_type=content_type,
            content=preview_text,
        )
        material = store.add_material_record(
            target_task_id,
            kind=material_kind,
            source={
                "type": "user_upload",
                "locator": filename,
                "uri": f"artifact://{artifact['id']}",
                "artifactId": artifact["id"],
                "sourceTaskId": target_task_id,
            },
            content_preview=compact_preview(preview_text),
            quality={
                "readable": preview_text is not None,
                "sourceBounded": True,
                "missingFields": missing_fields_for_upload(material_kind=material_kind, content=preview_text),
            },
            linked_artifact_ids=[artifact["id"]],
            created_by=identity["createdBy"],
            ingestion_mode="user_upload",
        )
        if material:
            store.add_memory_record(
                target_task_id,
                kind="user_fact",
                content=f"User uploaded material {filename} for task {target_task_id}.",
                linked_materials=[material["materialId"]],
                linked_artifacts=[artifact["id"]],
                confidence="high",
                created_by=identity["createdBy"],
            )
            store.add_memory_record(
                target_task_id,
                kind="artifact_summary",
                content=f"Upload artifact {artifact['id']} is bound to material {material['materialId']}.",
                linked_materials=[material["materialId"]],
                linked_artifacts=[artifact["id"]],
                confidence="high",
                created_by="material_ingest",
            )
            store.add_memory_record(
                target_task_id,
                kind="agent_inference",
                content=f"Material ingest classified {filename} as {material_kind}.",
                linked_materials=[material["materialId"]],
                linked_artifacts=[artifact["id"]],
                confidence="medium",
                created_by="material_ingest",
            )
        refreshed = store.get_task(target_task_id)
        if refreshed:
            artifact = next((a for a in refreshed.get("artifacts") or [] if a.get("id") == artifact["id"]), artifact)
    return ArtifactOut.model_validate(artifact)


@router.post("/{artifact_id}/reuse", response_model=ReuseArtifactOut)
def reuse_artifact_route(
    artifact_id: str,
    body: ReuseArtifactIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ReuseArtifactOut:
    source_task, source_artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(source_artifact, identity)
    assert source_task is not None
    ensure_task_in_scope(store.get_task(body.targetTaskId), identity)
    source_validation = validator_result_for_artifact(source_artifact)
    if source_validation.get("artifactState") != "usable" or not source_validation.get("usable"):
        raise HTTPException(
            status_code=409,
            detail="artifact is not usable for downstream reuse; review or supplement it first",
        )
    new_artifact = catalog_reuse_artifact(
        source_artifact,
        source_task,
        body.targetTaskId,
        title=body.title,
        summary=body.summary,
        identity=identity,
    )
    if not new_artifact:
        raise HTTPException(status_code=404, detail="target task not found")
    # reuses 关系由 catalog_reuse_artifact 按后端记录（postgres 经
    # ArtifactRelationRepository；store.record_artifact_relation 在 postgres 模式被禁用）。
    return ReuseArtifactOut(
        artifact=ArtifactOut.model_validate(new_artifact),
        sourceArtifactId=artifact_id,
        sourceTaskId=source_task["taskId"],
    )


@router.post("/{artifact_id}/review", response_model=ArtifactDetailOut)
def review_artifact_route(
    artifact_id: str,
    body: ReviewArtifactIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ArtifactDetailOut:
    if settings.persistence_backend == "postgres":
        try:
            updated = ArtifactRepository().review_artifact(
                artifact_id=artifact_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                decision=body.decision,
                reviewer=identity["createdBy"],
                note=body.note,
                source="user",
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        task = ensure_task_in_scope(TaskRepository().get_task(str(updated.get("sourceTaskId") or "")), identity)
        return _artifact_detail(task, updated)

    task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert task is not None
    next_state = "usable" if body.decision == "approved" else "rejected_or_needs_revision"
    reviewer = identity["createdBy"]
    try:
        updated = store.update_artifact_review_state(
            task["taskId"], artifact_id, artifact_state=next_state,
            reviewer=reviewer, note=body.note, source="user",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _artifact_detail(task, updated)


# ---------------------------------------------------------------------------
# Artifact versioning (issue #302): edit / regenerate / history / restore
# ---------------------------------------------------------------------------


def _artifact_content_is_editable(artifact: dict[str, Any]) -> tuple[bool, str]:
    if artifact.get("archivedAt"):
        return False, "archived artifacts cannot be edited"
    fmt = str(artifact.get("format") or "").lower()
    kind = str(artifact.get("kind") or "")
    path = str(artifact.get("path") or "").lower()
    inline = artifact.get("inlineContent")
    is_text = (
        fmt in {"markdown", "md", "text", "txt"}
        or kind == "research_start_draft"
        or path.endswith((".md", ".markdown", ".txt"))
        or isinstance(inline, str)
    )
    return (True, "") if is_text else (False, "only markdown/text artifacts can be edited inline")


def _store_postgres_version_bytes(
    *, task_id: str, artifact_id: str, version_id: str, data: bytes
):
    storage_ref = f"artifact-versions/{artifact_id}/{version_id}.md"
    return artifact_write_service.store_durable_bytes(
        task_id,
        storage_ref,
        data,
        "text/markdown; charset=utf-8",
    )


def _compensate_uncommitted_version_blob(
    *, repo: ArtifactRepository, task_id: str, artifact_id: str, stored: Any
) -> None:
    try:
        artifact_write_service.delete_durable_bytes(
            task_id,
            stored.storage_type,
            stored.storage_ref,
            storage_scope=stored.storage_scope,
        )
        return
    except Exception as exc:  # noqa: BLE001 - cleanup must become durable if direct delete fails
        cleanup_error = f"{type(exc).__name__}: {exc}"
    try:
        repo.enqueue_storage_cleanup(
            task_id=task_id,
            artifact_id=artifact_id,
            storage_type=stored.storage_type,
            storage_ref=stored.storage_ref,
            storage_scope=stored.storage_scope,
            reason_code="artifact_version_commit_failed",
            last_error=cleanup_error,
        )
    except Exception:
        _log.exception("failed to persist version blob cleanup receipt artifact=%s", artifact_id)


@router.get("/{artifact_id}/versions", response_model=ArtifactVersionsOut)
def list_artifact_versions(
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactVersionsOut:
    """Version history timeline (summaries only — no inlineContent)."""
    task, artifact = _read_artifact_context(artifact_id, identity)
    if settings.persistence_backend == "postgres":
        canonical_task = ensure_task_in_scope(_task_authority(task["taskId"]), identity)
        data = {
            "revision": int(canonical_task.get("revision") or 0),
            "currentVersionId": artifact.get("currentVersionId"),
            "versions": _canonical_artifact_versions(artifact),
        }
    else:
        task, _ = find_artifact_by_id(artifact_id)
        assert task is not None
        data = store.list_artifact_versions(task["taskId"], artifact_id)
        if data is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        data = dict(data)
        data["revision"] = int(task.get("revision") or 0)
    return ArtifactVersionsOut(
        artifactId=artifact_id,
        revision=data.get("revision"),
        currentVersionId=data.get("currentVersionId"),
        versions=data.get("versions") or [],
    )


@router.get("/{artifact_id}/versions/{version_id}", response_model=ArtifactVersionOut)
def get_artifact_version(
    artifact_id: str,
    version_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactVersionOut:
    """Return historical version metadata only; content has a separate gated route."""
    task, artifact = _read_artifact_context(artifact_id, identity)
    version = (
        _canonical_artifact_version(artifact, version_id)
        if settings.persistence_backend == "postgres"
        else store.get_artifact_version(task["taskId"], artifact_id, version_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    return ArtifactVersionOut.model_validate(version)


@router.get("/{artifact_id}/versions/{version_id}/content", response_model=ArtifactContentOut)
def get_artifact_version_content(
    artifact_id: str,
    version_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactContentOut:
    """Read a historical version's content (issue #302 review r4).

    Resolves the version snapshot the same way the live artifact is resolved —
    inline content, the version's immutable local file (each version gets its own
    file, so a regenerate re-run can't overwrite it), or a presigned external
    object. Lets the version drawer show large/local/external versions that have
    no inlineContent.
    """
    task, artifact = _read_artifact_context(artifact_id, identity)
    version = (
        _canonical_artifact_version(artifact, version_id)
        if settings.persistence_backend == "postgres"
        else store.get_artifact_version(task["taskId"], artifact_id, version_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    if version.get("source") == "revision_requested":
        raise HTTPException(status_code=404, detail="revision_requested is a metadata-only event with no content")
    if settings.persistence_backend == "postgres":
        try:
            version = _version_with_storage_reference(
                artifact_id=artifact_id, version=version
            )
            blob = _read_canonical_version_bytes(
                task_id=task["taskId"],
                artifact_id=artifact_id,
                version=version,
            )
            content = blob.decode("utf-8")
            source_mode = str(version.get("storageType") or "local")
        except (FileNotFoundError, NotImplementedError, PermissionError, UnicodeError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_content_unavailable", "artifactId": artifact_id, "versionId": version_id},
            ) from exc
    else:
        # JSON-store compatibility retains recorded-run fallback for demo data.
        pseudo: dict[str, Any] = {
            "id": artifact_id,
            "storageType": version.get("storageType"),
            "inlineContent": version.get("inlineContent"),
            "path": version.get("path"),
            "externalKey": version.get("externalKey"),
            "contentSha256": version.get("contentSha256"),
            "contentSize": version.get("contentSize"),
            "format": artifact.get("format"),
            "relatedSkillId": artifact.get("relatedSkillId"),
        }
        try:
            content, source_mode = load_artifact_content(
                task["taskId"],
                pseudo,
                task.get("capabilityCalls") or [],
                allow_replay_fallback=True,
            )
        except (FileNotFoundError, NotImplementedError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ArtifactContentOut(
        artifactId=artifact_id,
        path=version.get("path") or "",
        format=artifact.get("format"),
        sourceMode=cast(Literal["live", "replay", "external", "inline", "local", "object"], source_mode),
        content=content,
        evidenceRefs=artifact.get("evidenceRefs") or [],
    )


@router.patch("/{artifact_id}/content", response_model=ArtifactDetailOut)
def edit_artifact_content(
    artifact_id: str,
    body: EditArtifactContentIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
) -> ArtifactDetailOut:
    """Inline-edit Markdown/text content → new user_edit version (original preserved)."""
    if settings.persistence_backend == "postgres":
        if not isinstance(body.content, str) or not body.content.strip():
            raise HTTPException(status_code=409, detail="content must not be empty")
        task, artifact = _read_artifact_context(artifact_id, identity)
        if expected_revision is None:
            raise HTTPException(status_code=428, detail="artifact_revision_precondition_required")
        editable, reason = _artifact_content_is_editable(artifact)
        if not editable:
            raise HTTPException(status_code=409, detail=reason)
        data = body.content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        version_id = f"v_{uuid4().hex[:12]}"
        try:
            stored = _store_postgres_version_bytes(
                task_id=task["taskId"], artifact_id=artifact_id, version_id=version_id, data=data
            )
        except ArtifactStorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        repo = ArtifactRepository()
        try:
            updated = repo.append_content_version(
                artifact_id=artifact_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                created_by=identity["createdBy"],
                version_id=version_id,
                source="user_edit",
                storage_type=cast(Literal["external", "local"], stored.storage_type),
                storage_ref=stored.storage_ref,
                storage_scope=stored.storage_scope,
                content_sha256=digest,
                content_size=len(data),
                feedback=body.changeReason,
                expected_revision=expected_revision,
            )
        except (KeyError, PermissionError) as exc:
            _compensate_uncommitted_version_blob(
                repo=repo, task_id=task["taskId"], artifact_id=artifact_id, stored=stored
            )
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except (RuntimeError, ValueError) as exc:
            _compensate_uncommitted_version_blob(
                repo=repo, task_id=task["taskId"], artifact_id=artifact_id, stored=stored
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception:
            _compensate_uncommitted_version_blob(
                repo=repo, task_id=task["taskId"], artifact_id=artifact_id, stored=stored
            )
            raise
        return _artifact_detail(task, updated)
    task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert task is not None
    try:
        updated = store.edit_artifact_content(
            task["taskId"],
            artifact_id,
            content=body.content,
            change_reason=body.changeReason,
            editor=identity["createdBy"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _artifact_detail(task, updated)


@router.post("/{artifact_id}/regenerate", response_model=ArtifactDetailOut)
async def regenerate_artifact(
    artifact_id: str,
    body: RegenerateArtifactIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactDetailOut:
    """Reject legacy regeneration until a durable execution gate is available."""
    if settings.persistence_backend == "postgres":
        _read_artifact_context(artifact_id, identity)
    else:
        _task, artifact = find_artifact_by_id(artifact_id)
        ensure_artifact_in_scope(artifact, identity)
    raise HTTPException(status_code=409, detail="artifact_regeneration_requires_durable_gate")


@router.post("/{artifact_id}/versions/{version_id}/restore", response_model=ArtifactDetailOut)
def restore_artifact_version(
    artifact_id: str,
    version_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
) -> ArtifactDetailOut:
    """Restore a historical content version → new restore version (history kept)."""
    if settings.persistence_backend == "postgres":
        task, artifact = _read_artifact_context(artifact_id, identity)
        if expected_revision is None:
            raise HTTPException(status_code=428, detail="artifact_revision_precondition_required")
        target = _canonical_artifact_version(artifact, version_id)
        if target is None:
            raise HTTPException(status_code=404, detail="version not found")
        if target.get("source") == "revision_requested":
            raise HTTPException(status_code=409, detail="cannot restore a metadata-only revision_requested event")
        try:
            target = _version_with_storage_reference(artifact_id=artifact_id, version=target)
            data = _read_canonical_version_bytes(
                task_id=task["taskId"], artifact_id=artifact_id, version=target
            )
        except (ArtifactStorageError, FileNotFoundError, NotImplementedError, PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="artifact_version_content_unavailable") from exc
        digest = hashlib.sha256(data).hexdigest()
        new_version_id = f"v_{uuid4().hex[:12]}"
        try:
            stored = _store_postgres_version_bytes(
                task_id=task["taskId"], artifact_id=artifact_id, version_id=new_version_id, data=data
            )
        except ArtifactStorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        repo = ArtifactRepository()
        try:
            updated = repo.append_content_version(
                artifact_id=artifact_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                created_by=identity["createdBy"],
                version_id=new_version_id,
                source="restore",
                storage_type=cast(Literal["external", "local"], stored.storage_type),
                storage_ref=stored.storage_ref,
                storage_scope=stored.storage_scope,
                content_sha256=digest,
                content_size=len(data),
                feedback=f"恢复自版本 v{target.get('versionNumber')}",
                restored_from_version_id=version_id,
                expected_revision=expected_revision,
            )
        except (KeyError, PermissionError) as exc:
            _compensate_uncommitted_version_blob(
                repo=repo, task_id=task["taskId"], artifact_id=artifact_id, stored=stored
            )
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except (RuntimeError, ValueError) as exc:
            _compensate_uncommitted_version_blob(
                repo=repo, task_id=task["taskId"], artifact_id=artifact_id, stored=stored
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception:
            _compensate_uncommitted_version_blob(
                repo=repo, task_id=task["taskId"], artifact_id=artifact_id, stored=stored
            )
            raise
        return _artifact_detail(task, updated)
    task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert task is not None
    try:
        updated = store.restore_artifact_version(
            task["taskId"],
            artifact_id,
            version_id,
            requested_by=identity["createdBy"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _artifact_detail(task, updated)


# ---------------------------------------------------------------------------
# Artifact scope / ownership / materials (dev)
# ---------------------------------------------------------------------------


@router.post("/{artifact_id}/confirm-scope-expansion", response_model=ArtifactDetailOut)
def confirm_scope_expansion_route(
    artifact_id: str,
    body: ConfirmScopeExpansionIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ArtifactDetailOut:
    reviewer = identity["createdBy"]
    if settings.persistence_backend == "postgres":
        try:
            updated = ArtifactRepository().confirm_scope_expansion(
                artifact_id=artifact_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                reviewer=reviewer,
                note=body.note,
                source="user",
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        except (KeyError, PermissionError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        task = ensure_task_in_scope(TaskRepository().get_task(str(updated.get("sourceTaskId") or "")), identity)
        return _artifact_detail(task, updated)

    task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert task is not None
    try:
        updated = store.confirm_artifact_scope_expansion(
            task["taskId"], artifact_id, reviewer=reviewer, note=body.note, source="user",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _artifact_detail(task, updated)


@router.patch("/{artifact_id}/ownership", response_model=ArtifactDetailOut)
def reassign_artifact_ownership_route(
    artifact_id: str,
    body: ReassignArtifactIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactDetailOut:
    source_task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert source_task is not None
    target_task = ensure_task_in_scope(store.get_task(body.targetTaskId), identity)
    current_task = ensure_task_in_scope(store.get_task(body.currentTaskId), identity) if body.currentTaskId else source_task
    confirmation = _ownership_confirmation_detail(source_task=current_task, target_task=target_task)
    if confirmation and not body.ownershipConfirmed:
        raise HTTPException(status_code=409, detail=confirmation)
    updated = store.reassign_artifact_task(artifact_id, body.targetTaskId, note=body.note)
    if not updated:
        raise HTTPException(status_code=404, detail="artifact not found")
    detail = dict(updated)
    detail.setdefault("origin", "generated")
    detail["sourceTaskId"] = body.targetTaskId
    detail["sourceTaskTitle"] = target_task.get("title")
    return ArtifactDetailOut.model_validate(detail)


@router.patch("/{artifact_id}/materials", response_model=ArtifactDetailOut)
def update_artifact_materials_route(
    artifact_id: str,
    body: UpdateArtifactMaterialsIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ArtifactDetailOut:
    task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert task is not None
    updated = store.update_artifact_material_binding(task["taskId"], artifact_id, body.linkedMaterialIds)
    if not updated:
        raise HTTPException(status_code=404, detail="artifact not found")
    if body.note:
        store.add_memory_record(
            task["taskId"],
            kind="decision",
            content=body.note,
            linked_materials=updated.get("linkedMaterialIds") or [],
            linked_artifacts=[artifact_id],
            confidence="high",
            created_by=identity["createdBy"],
        )
        refreshed = store.get_task(task["taskId"])
        if refreshed:
            updated = next((a for a in refreshed.get("artifacts") or [] if a.get("id") == artifact_id), updated)
    detail = dict(updated)
    detail.setdefault("origin", "generated")
    detail["sourceTaskId"] = task["taskId"]
    detail["sourceTaskTitle"] = task.get("title")
    return ArtifactDetailOut.model_validate(detail)


@router.delete("/{artifact_id}", response_model=None)
def delete_artifact_route(
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    expected_revision: int | None = Header(default=None, alias="If-Match-Revision"),
) -> dict[str, bool]:
    if settings.persistence_backend == "postgres":
        try:
            artifact = ArtifactRepository().delete_artifact_scoped(
                artifact_id=artifact_id,
                owner_id=identity["ownerId"],
                workspace_id=identity["workspaceId"],
                expected_revision=expected_revision,
            )
        except (KeyError, PermissionError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # PostgreSQL delete commits only catalog/reference changes plus cleanup
        # intent. The fenced cleanup consumer owns all external object deletion.
        return {"deleted": True}

    task, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    assert task is not None
    removed = store.remove_artifact(task["taskId"], artifact_id)
    if not removed:
        raise HTTPException(status_code=404, detail="artifact not found")
    _cleanup_uploaded_storage(
        task["taskId"],
        str(artifact.get("storageType") or "local"),
        artifact.get("externalKey"),
        artifact.get("path"),
    )
    return {"deleted": True}


@router.get("/{artifact_id}/relations", response_model=ArtifactRelationsOut)
def get_artifact_relations(
    artifact_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
    direction: str | None = Query(default=None),
    relationType: str | None = Query(default=None),
) -> ArtifactRelationsOut:
    """来源链：返回该产物的出入关系，enrich 对端的 title/kind。"""
    _, artifact = find_artifact_by_id(artifact_id)
    ensure_artifact_in_scope(artifact, identity)
    raw = store.list_artifact_relations(
        artifact_id,
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        direction=direction or "both",
        relation_type=relationType,
    )
    out: list[ArtifactRelationOut] = []
    for r in raw:
        src = r.get("sourceArtifactId")
        tgt = r.get("targetArtifactId")
        if src == artifact_id:
            other_id, dirn = tgt, "outgoing"
        elif tgt == artifact_id:
            other_id, dirn = src, "incoming"
        else:
            continue
        if not other_id:
            continue
        _, other = find_artifact_by_id(other_id)
        out.append(
            ArtifactRelationOut(
                relationType=r.get("relationType") or "",
                direction=dirn,  # type: ignore[arg-type]
                artifactId=other_id,
                title=other.get("title") if other else None,
                kind=other.get("kind") if other else None,
                capabilityCallId=r.get("capabilityCallId"),
                createdAt=r.get("createdAt"),
            )
        )
    return ArtifactRelationsOut(relations=out)


# ---------------------------------------------------------------------------
# Artifact-04 通道3：异步训练完成 webhook（HMAC 鉴权，非 worker token / 非 user 身份）
# ---------------------------------------------------------------------------


@router.post("/ingest/callback", response_model=None)
def ingest_callback(body: IngestCallbackIn) -> dict[str, str]:
    from app.modules.artifact_ingest import verify_callback

    # 1) HMAC 校验（绑定到 task+call）
    if not verify_callback(body.task_id, body.capability_call_id, body.signature):
        raise HTTPException(status_code=401, detail="invalid callback signature")

    # 2) job 归属：capability_call_id 必须属于该 task
    task = store.get_task(body.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if not any(c.get("id") == body.capability_call_id for c in task.get("capabilityCalls") or []):
        raise HTTPException(status_code=403, detail="capability call not bound to this task")

    # 3) 登记产物（reference 或 已-presigned-上传）
    extra: dict[str, Any] = {"origin": "remote_compute"}
    title = body.title
    storage_type: str | None = None
    if body.external_uri:
        extra.update({"storageType": "external", "externalProvider": body.external_provider or "url", "externalUri": body.external_uri})
        title = title or body.external_uri
    elif body.object_key:
        storage_type = "external"
        extra.update({"externalKey": body.object_key, "contentSize": body.size})
        if body.sha256:
            extra["contentSha256"] = body.sha256
        title = title or body.object_key
    else:
        raise HTTPException(status_code=400, detail="must provide external_uri or object_key")

    artifact = store.add_artifact(
        body.task_id,
        kind=body.kind,
        title=title,
        summary=body.summary,
        storage_type=storage_type,
        source_capability_call_id=body.capability_call_id,
        extra_fields=extra,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="task not found")

    # 4) 完工：capability call → completed
    store.patch_capability_call(body.task_id, body.capability_call_id, status="completed")
    return {"status": "ok", "artifactId": artifact["id"]}

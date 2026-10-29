"""Research Package v0.2 asset lifecycle routes.

Import only parses, validates and persists bytes. It deliberately has no path to
execution, Evidence acceptance, ScientificState mutation or ResearchDesign.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from app.auth import RequestIdentity, ensure_task_in_scope, resolve_request_identity
from app.config import settings
from app.models.research_package import ResearchPackageAssetOut, ResearchPackageCreateIn
from app.modules.research_package_asset import (
    ResearchPackageAsset,
    ResearchPackageAssetService,
    ResearchPackageConflictError,
    ResearchPackageNotFoundError,
)
from app.modules.research_package_protocol import ResearchPackageValidationError
from app.modules.task_repository import TaskRepository


router = APIRouter(prefix="/research-packages", tags=["research-packages"])


def _source_task_in_scope(source_task_id: str | None, identity: RequestIdentity) -> str | None:
    normalized = str(source_task_id or "").strip()
    if not normalized:
        return None
    ensure_task_in_scope(TaskRepository().get_task(normalized), identity)
    return normalized


def _out(asset: ResearchPackageAsset) -> ResearchPackageAssetOut:
    document = asset.package.document
    return ResearchPackageAssetOut(
        artifactId=asset.artifact_id,
        packageId=asset.package.package_id,
        version=asset.package.version,
        specVersion=asset.package.spec_version,
        name=str(document["name"]),
        summary=str(document["summary"]),
        contentSha256=asset.package.content_sha256,
        sourceTaskId=asset.source_task_id,
        package=document,
    )


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResearchPackageValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": exc.code, "path": exc.path},
        )
    if isinstance(exc, ResearchPackageNotFoundError):
        return HTTPException(status_code=404, detail="research_package_not_found")
    if isinstance(exc, ResearchPackageConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


@router.post("", response_model=ResearchPackageAssetOut, status_code=201)
def save_research_package(
    body: ResearchPackageCreateIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchPackageAssetOut:
    source_task_id = _source_task_in_scope(body.sourceTaskId, identity)
    try:
        asset = ResearchPackageAssetService().save(
            body.package,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            created_by=identity["createdBy"],
            source_task_id=source_task_id,
        )
    except (ResearchPackageValidationError, ResearchPackageNotFoundError, ResearchPackageConflictError) as exc:
        raise _translate_error(exc) from exc
    return _out(asset)


@router.post("/import", response_model=ResearchPackageAssetOut, status_code=201)
async def import_research_package(
    file: UploadFile = File(...),
    sourceTaskId: str | None = Form(None),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchPackageAssetOut:
    raw = await file.read(settings.artifact_upload_max_bytes + 1)
    if len(raw) > settings.artifact_upload_max_bytes:
        raise HTTPException(status_code=413, detail="file exceeds upload size limit")
    source_task_id = _source_task_in_scope(sourceTaskId, identity)
    suffix = Path(file.filename or "").suffix.lower().lstrip(".") or None
    try:
        asset = ResearchPackageAssetService().import_bytes(
            raw,
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            created_by=identity["createdBy"],
            format_hint=suffix,
            source_task_id=source_task_id,
        )
    except (ResearchPackageValidationError, ResearchPackageNotFoundError, ResearchPackageConflictError) as exc:
        raise _translate_error(exc) from exc
    return _out(asset)


@router.get("", response_model=ResearchPackageAssetOut)
def read_research_package(
    packageId: str = Query(...),
    version: str = Query(...),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchPackageAssetOut:
    try:
        asset = ResearchPackageAssetService().read(
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            package_id=packageId,
            version=version,
        )
    except (ResearchPackageValidationError, ResearchPackageNotFoundError, ResearchPackageConflictError) as exc:
        raise _translate_error(exc) from exc
    return _out(asset)


@router.get("/export")
def export_research_package(
    packageId: str = Query(...),
    version: str = Query(...),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> Response:
    try:
        content = ResearchPackageAssetService().export_bytes(
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            package_id=packageId,
            version=version,
        )
    except (ResearchPackageValidationError, ResearchPackageNotFoundError, ResearchPackageConflictError) as exc:
        raise _translate_error(exc) from exc
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="research-package.json"'},
    )

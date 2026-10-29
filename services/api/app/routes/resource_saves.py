"""Authenticated, PostgreSQL-backed saves for Resource Plaza resources."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import RequestIdentity, resolve_request_identity
from app.config import settings
from app.models.resource_catalog import ResourceCatalogItemOut
from app.models.resource_saves import ResourceSaveListOut, ResourceSaveStateOut, ResourceSaveStatusOut
from app.modules.resource_catalog import ResourceCatalogUnavailableError
from app.modules.resource_save_repository import ResourceSaveRepository
from app.modules.resource_saves import (
    get_public_resource,
    list_saved_public_resources,
    saved_public_resource_ids,
)


MAX_STATUS_RESOURCE_IDS = 100

router = APIRouter(prefix="/resource-saves", tags=["resource-saves"])


def _require_postgres() -> None:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="resource_saves_requires_postgres")


def _catalog_unavailable(exc: ResourceCatalogUnavailableError) -> HTTPException:
    return HTTPException(status_code=503, detail="resource_catalog_unavailable")


@router.get("", response_model=ResourceSaveListOut)
def list_resource_saves(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=24, ge=1, le=100),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResourceSaveListOut:
    _require_postgres()
    try:
        items = list_saved_public_resources(
            ResourceSaveRepository(),
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
        )
    except ResourceCatalogUnavailableError as exc:
        raise _catalog_unavailable(exc) from exc
    start = (page - 1) * pageSize
    return ResourceSaveListOut(
        items=[ResourceCatalogItemOut.model_validate(item) for item in items[start:start + pageSize]],
        page=page,
        pageSize=pageSize,
        total=len(items),
    )


@router.get("/status", response_model=ResourceSaveStatusOut)
def get_resource_save_status(
    resourceId: list[str] = Query(default_factory=list),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResourceSaveStatusOut:
    _require_postgres()
    if len(resourceId) > MAX_STATUS_RESOURCE_IDS:
        raise HTTPException(status_code=422, detail="too many resourceId values")
    resource_ids = list(dict.fromkeys(resource_id for resource_id in resourceId if resource_id))
    try:
        saved_ids = saved_public_resource_ids(
            ResourceSaveRepository(),
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            resource_ids=resource_ids,
        )
    except ResourceCatalogUnavailableError as exc:
        raise _catalog_unavailable(exc) from exc
    return ResourceSaveStatusOut(savedResourceIds=saved_ids)


@router.put("/{resource_id}", response_model=ResourceSaveStateOut)
def save_resource(
    resource_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResourceSaveStateOut:
    _require_postgres()
    try:
        resource = get_public_resource(resource_id)
    except ResourceCatalogUnavailableError as exc:
        raise _catalog_unavailable(exc) from exc
    if resource is None:
        raise HTTPException(status_code=404, detail="resource not found")
    ResourceSaveRepository().save(
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        resource_id=resource_id,
    )
    return ResourceSaveStateOut(resourceId=resource_id, saved=True)


@router.delete("/{resource_id}", response_model=ResourceSaveStateOut)
def remove_resource_save(
    resource_id: str,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResourceSaveStateOut:
    _require_postgres()
    ResourceSaveRepository().remove(
        owner_id=identity["ownerId"],
        workspace_id=identity["workspaceId"],
        resource_id=resource_id,
    )
    return ResourceSaveStateOut(resourceId=resource_id, saved=False)

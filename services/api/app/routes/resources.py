"""Read-only Resource Catalog routes."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from app.auth import RequestIdentity, resolve_request_identity
from app.models.resource_catalog import (
    ResourceAvailability,
    ResourceCatalogItemOut,
    ResourceCatalogListOut,
    ResourceCatalogSort,
    ResourceDomain,
    ResourceScenario,
    ResourceType,
)
from app.modules.resource_catalog import (
    ResourceCatalogUnavailableError,
    get_resource,
    get_resource_for_research_start,
    list_resources,
)
from app.modules.intent_session_store import intent_session_store


router = APIRouter(prefix="/resources", tags=["resources"])


class ResearchStartIntentOut(BaseModel):
    intentId: str
    researchStartUrl: str


class ResourceResearchStartIntentIn(BaseModel):
    """The URL identifies the resource; clients cannot submit resource identity."""

    model_config = ConfigDict(extra="forbid")


@router.get("", response_model=ResourceCatalogListOut)
def list_resource_catalog(
    q: str | None = Query(default=None),
    resourceType: ResourceType | None = Query(default=None),
    availability: ResourceAvailability | None = Query(default=None),
    domain: ResourceDomain | None = Query(default=None),
    scenario: ResourceScenario | None = Query(default=None),
    visibleOnHome: bool | None = Query(default=None),
    sort: ResourceCatalogSort = Query(default=ResourceCatalogSort.RELEVANCE),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
) -> ResourceCatalogListOut:
    try:
        rows = list_resources(
            q=q,
            resource_type=resourceType,
            availability=availability,
            domain=domain,
            scenario=scenario,
            visible_on_home=visibleOnHome,
            sort=sort,
        )
    except ResourceCatalogUnavailableError as exc:
        raise HTTPException(status_code=503, detail="resource_catalog_unavailable") from exc
    total = len(rows)
    start = (page - 1) * pageSize
    return ResourceCatalogListOut(
        resources=[ResourceCatalogItemOut.model_validate(row) for row in rows[start:start + pageSize]],
        total=total,
        page=page,
        pageSize=pageSize,
    )


@router.get("/{resource_id}", response_model=ResourceCatalogItemOut)
def get_resource_catalog_item(resource_id: str) -> ResourceCatalogItemOut:
    try:
        row = get_resource(resource_id)
    except ResourceCatalogUnavailableError as exc:
        raise HTTPException(status_code=503, detail="resource_catalog_unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="resource not found")
    return ResourceCatalogItemOut.model_validate(row)


@router.post("/{resource_id}/research-start-intent", response_model=ResearchStartIntentOut)
def create_resource_research_start_intent(
    resource_id: str,
    body: ResourceResearchStartIntentIn | None = Body(default=None),
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> ResearchStartIntentOut:
    """Hand a catalog selection to Research Start without accepting client identity."""
    _ = body
    try:
        resource = get_resource_for_research_start(resource_id)
    except ResourceCatalogUnavailableError as exc:
        raise HTTPException(status_code=503, detail="resource_catalog_unavailable") from exc
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resource not found")
    try:
        session = intent_session_store.create_catalog_resource_intent(
            owner_id=identity["ownerId"],
            workspace_id=identity["workspaceId"],
            created_by=identity["createdBy"],
            resource_id=resource["id"],
            resource_version=str(resource["releaseVersion"]),
            capability_id=resource["capabilityId"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="resource unavailable") from exc
    intent_id = session["sessionId"]
    return ResearchStartIntentOut(
        intentId=intent_id,
        researchStartUrl=f"/?intent=create&sessionId={intent_id}",
    )

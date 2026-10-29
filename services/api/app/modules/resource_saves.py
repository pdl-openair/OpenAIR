"""Public-resource validation and saved-resource projections."""

from __future__ import annotations

from typing import Any

from app.modules.resource_catalog import ResourceCatalogUnavailableError, get_resource
from app.modules.resource_save_repository import ResourceSaveRepository


def get_public_resource(resource_id: str) -> dict[str, Any] | None:
    """Use the catalog's single public-read authority for save eligibility."""
    return get_resource(resource_id)


def list_saved_public_resources(
    repository: ResourceSaveRepository,
    *,
    owner_id: str,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Resolve saved ids through the catalog again so stale records fail closed."""
    items: list[dict[str, Any]] = []
    for resource_id in repository.list_resource_ids(
        owner_id=owner_id,
        workspace_id=workspace_id,
    ):
        resource = get_public_resource(resource_id)
        if resource is not None:
            items.append(resource)
    return items


def saved_public_resource_ids(
    repository: ResourceSaveRepository,
    *,
    owner_id: str,
    workspace_id: str,
    resource_ids: list[str],
) -> list[str]:
    """Return caller-owned saves that remain publicly readable."""
    saved_ids = set(
        repository.saved_resource_ids(
            owner_id=owner_id,
            workspace_id=workspace_id,
            resource_ids=resource_ids,
        )
    )
    return [
        resource_id
        for resource_id in resource_ids
        if resource_id in saved_ids and get_public_resource(resource_id) is not None
    ]

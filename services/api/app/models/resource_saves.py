"""API response contracts for persisted Resource Plaza saves."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.resource_catalog import ResourceCatalogItemOut


class ResourceSaveStateOut(BaseModel):
    resourceId: str
    saved: bool


class ResourceSaveStatusOut(BaseModel):
    savedResourceIds: list[str] = Field(default_factory=list)


class ResourceSaveListOut(BaseModel):
    items: list[ResourceCatalogItemOut] = Field(default_factory=list)
    page: int
    pageSize: int
    total: int

from __future__ import annotations

import pytest

from app.models.resource_catalog import ResourceAvailability, ResourceType
from app.modules import resource_catalog


def _resource(resource_id: str) -> resource_catalog.CatalogResource:
    return resource_catalog.CatalogResource(
        resource_id=resource_id,
        name=resource_id,
        description="Catalog identity test resource",
        resource_type=ResourceType.SKILL,
        input=("question",),
        output=("result",),
        trust_summary="test",
        requirements=(),
        limitations=(),
        availability=ResourceAvailability.VIEW_ONLY,
        execution_blocked=True,
        safety_risk=None,
        primary_action="view",
        keywords=(),
    )


def test_approved_public_contribution_is_discoverable_but_not_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_id = "skill:community-public-resource"

    class _ContributionRepo:
        def list_public_catalog(self, *, limit: int, offset: int):
            if offset:
                return []
            return [{
                "contribution_id": "contribution-1",
                "catalog_resource_id": resource_id,
                "release_version": "1.0.0",
                "title": "Community public resource",
                "description": "Public contribution without runtime authority.",
                "domain": "general",
                "resource_ref": "community/public-resource",
                "published_manifest": {
                    "description": "Public contribution without runtime authority.",
                    "input": ["question"],
                    "output": ["answer"],
                },
            }]

    monkeypatch.setattr(resource_catalog, "get_contribution_repository", lambda: _ContributionRepo())
    monkeypatch.setattr(resource_catalog, "_load_catalog_releases", lambda: {})
    monkeypatch.setattr(resource_catalog, "_project_skills", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_agents", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_workflows", lambda: [])

    rows = resource_catalog._build_catalog_resources()

    assert len(rows) == 1
    row = rows[0]
    assert row.resource_id == resource_id
    assert row.availability == ResourceAvailability.CONFIGURATION_REQUIRED
    assert row.execution_blocked is True
    assert row.durable_executable is False
    assert row.release is not None
    assert row.release.execution_entry is None
    assert row.release.source == "用户公开贡献"
    assert row.release.maintainer == "OpenAIR 社区"


def test_public_contribution_cannot_shadow_official_catalog_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_id = "skill:catalog-id-collision"
    official = _resource(resource_id)
    contribution = _resource(resource_id)

    monkeypatch.setattr(resource_catalog, "_load_catalog_releases", lambda: {resource_id: object()})
    monkeypatch.setattr(resource_catalog, "_project_skills", lambda: [official])
    monkeypatch.setattr(resource_catalog, "_project_agents", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_workflows", lambda: [])
    monkeypatch.setattr(resource_catalog, "_apply_catalog_release", lambda row, _release: row)
    monkeypatch.setattr(resource_catalog, "_apply_runtime_facts", lambda row: row)
    monkeypatch.setattr(resource_catalog, "_project_approved_contributions", lambda: [contribution])

    with pytest.raises(
        resource_catalog.ResourceCatalogUnavailableError,
        match="Public catalog contains duplicate resource IDs",
    ):
        resource_catalog._build_catalog_resources()

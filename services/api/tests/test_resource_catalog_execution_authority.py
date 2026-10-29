from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.resource_catalog import ResourceAvailability, ResourceType
from app.modules import resource_catalog
from app.modules.resource_capability_facts import ResourceCapabilityFacts


client = TestClient(app)


@dataclass(frozen=True)
class _CatalogCase:
    resource_id: str
    release_availability: ResourceAvailability
    facts: ResourceCapabilityFacts
    expected_availability: ResourceAvailability
    expected_start_status: int
    resource_type: ResourceType = ResourceType.SKILL
    execution_entry: str | None = None


def _registry_resource(case: _CatalogCase) -> resource_catalog.CatalogResource:
    return resource_catalog.CatalogResource(
        resource_id=case.resource_id,
        name=case.resource_id,
        description=f"Catalog test resource {case.resource_id}",
        resource_type=case.resource_type,
        input=("question",),
        output=("result",),
        trust_summary="registry",
        requirements=(),
        limitations=(),
        availability=ResourceAvailability.VIEW_ONLY,
        execution_blocked=True,
        safety_risk=None,
        primary_action="view",
        keywords=(case.resource_id,),
    )


def _release(case: _CatalogCase) -> resource_catalog.CatalogRelease:
    state = {
        ResourceAvailability.TRYABLE: (False, "try", None),
        ResourceAvailability.CONFIGURATION_REQUIRED: (True, "configure", None),
        ResourceAvailability.VIEW_ONLY: (True, "view", None),
        ResourceAvailability.UNAVAILABLE: (True, "none", "execution_blocked"),
    }[case.release_availability]
    default_entry = {
        ResourceType.SKILL: "durable_skill",
        ResourceType.AGENT: "durable_agent",
    }.get(case.resource_type)
    execution_entry = case.execution_entry
    if execution_entry is None and case.release_availability in {
        ResourceAvailability.TRYABLE,
        ResourceAvailability.CONFIGURATION_REQUIRED,
    }:
        execution_entry = default_entry
    release = resource_catalog.CatalogRelease(
        resource_id=case.resource_id,
        resource_type=case.resource_type,
        release_version="1.0.0",
        catalog_released=True,
        discoverable=True,
        visible_on_home=False,
        visible_in_catalog=True,
        visible_in_detail=True,
        source="OpenAIR official",
        maintainer="OpenAIR",
        usage_policy="test",
        license_summary="test",
        evidence_status="verified",
        evidence_conflict_disposition="none",
        availability=case.release_availability,
        execution_blocked=state[0],
        primary_action=state[1],
        configuration_requirements=(),
        permission_requirements=("authenticated",),
        execution_entry=execution_entry,
        safety_risk=state[2],
        domains=("general",),
        scenarios=("problem_modeling",),
    )
    resource_catalog._validate_release_state(release)
    return release


def _install_catalog(monkeypatch: pytest.MonkeyPatch, cases: list[_CatalogCase]) -> None:
    registry = [_registry_resource(case) for case in cases]
    releases = {case.resource_id: _release(case) for case in cases}
    facts = {case.resource_id: case.facts for case in cases}

    monkeypatch.setattr(
        resource_catalog,
        "_project_skills",
        lambda: [row for row in registry if row.resource_type == ResourceType.SKILL],
    )
    monkeypatch.setattr(
        resource_catalog,
        "_project_agents",
        lambda: [row for row in registry if row.resource_type == ResourceType.AGENT],
    )
    monkeypatch.setattr(resource_catalog, "_project_workflows", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_approved_contributions", lambda: [])
    monkeypatch.setattr(resource_catalog, "_load_catalog_releases", lambda: releases)
    monkeypatch.setattr(
        resource_catalog,
        "resolve_resource_capability_facts",
        lambda resource_id, resource_type: facts[resource_id],
    )
    resource_catalog.clear_resource_catalog_cache()


def _healthy_facts(capability_id: str, launch_mode: str = "durable_skill") -> ResourceCapabilityFacts:
    return ResourceCapabilityFacts(
        capability_id=capability_id,
        durable_executable=True,
        launch_mode=launch_mode,
        dependency_gaps=(),
        unavailable_reason=None,
        runtime_family="test",
    )


def test_effective_state_is_single_authority_for_list_detail_filter_and_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        _CatalogCase(
            "skill:view-only-healthy",
            ResourceAvailability.VIEW_ONLY,
            _healthy_facts("view-only-healthy"),
            ResourceAvailability.VIEW_ONLY,
            404,
        ),
        _CatalogCase(
            "skill:configuration-required-healthy",
            ResourceAvailability.CONFIGURATION_REQUIRED,
            _healthy_facts("configuration-required-healthy"),
            ResourceAvailability.CONFIGURATION_REQUIRED,
            404,
        ),
        _CatalogCase(
            "skill:tryable-healthy",
            ResourceAvailability.TRYABLE,
            _healthy_facts("tryable-healthy"),
            ResourceAvailability.TRYABLE,
            200,
        ),
        _CatalogCase(
            "skill:tryable-gap",
            ResourceAvailability.TRYABLE,
            ResourceCapabilityFacts(
                capability_id="tryable-gap",
                durable_executable=True,
                launch_mode="durable_skill",
                dependency_gaps=("provider missing",),
                unavailable_reason=None,
                runtime_family="test",
            ),
            ResourceAvailability.CONFIGURATION_REQUIRED,
            404,
        ),
        _CatalogCase(
            "skill:tryable-not-durable",
            ResourceAvailability.TRYABLE,
            ResourceCapabilityFacts(
                capability_id="tryable-not-durable",
                durable_executable=False,
                launch_mode=None,
                dependency_gaps=(),
                unavailable_reason="durable contract unavailable",
                runtime_family="test",
            ),
            ResourceAvailability.UNAVAILABLE,
            404,
        ),
        _CatalogCase(
            "skill:tryable-runtime-unavailable",
            ResourceAvailability.TRYABLE,
            ResourceCapabilityFacts(
                capability_id="tryable-runtime-unavailable",
                durable_executable=True,
                launch_mode="durable_skill",
                dependency_gaps=(),
                unavailable_reason="runtime unavailable",
                runtime_family="test",
            ),
            ResourceAvailability.UNAVAILABLE,
            404,
        ),
        _CatalogCase(
            "agent:tryable-healthy",
            ResourceAvailability.TRYABLE,
            _healthy_facts("tryable-agent", "durable_agent"),
            ResourceAvailability.TRYABLE,
            200,
            resource_type=ResourceType.AGENT,
        ),
    ]
    _install_catalog(monkeypatch, cases)

    listed = client.get("/api/resources?pageSize=100")
    assert listed.status_code == 200
    rows = {row["id"]: row for row in listed.json()["resources"]}
    assert set(rows) == {case.resource_id for case in cases}

    for case in cases:
        row = rows[case.resource_id]
        assert row["availability"] == case.expected_availability.value
        assert row["executionBlocked"] is (case.expected_availability != ResourceAvailability.TRYABLE)
        detail = client.get(f"/api/resources/{case.resource_id}")
        assert detail.status_code == 200
        assert detail.json() == row
        started = client.post(f"/api/resources/{case.resource_id}/research-start-intent")
        assert started.status_code == case.expected_start_status
        assert (row["availability"] == ResourceAvailability.TRYABLE.value) is (started.status_code == 200)
        if started.status_code == 200:
            payload = started.json()
            assert payload["researchStartUrl"] == f"/?intent=create&sessionId={payload['intentId']}"

    tryable = client.get("/api/resources?availability=tryable&pageSize=100")
    assert tryable.status_code == 200
    assert {row["id"] for row in tryable.json()["resources"]} == {
        "skill:tryable-healthy",
        "agent:tryable-healthy",
    }


def test_runtime_launch_mode_mismatch_downgrades_instead_of_crossing_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _CatalogCase(
        "skill:wrong-runtime-entry",
        ResourceAvailability.TRYABLE,
        _healthy_facts("wrong-runtime-entry", "durable_agent"),
        ResourceAvailability.UNAVAILABLE,
        404,
    )
    _install_catalog(monkeypatch, [case])

    row = client.get(f"/api/resources/{case.resource_id}").json()
    assert row["availability"] == "unavailable"
    assert row["executionBlocked"] is True
    assert row["unavailableReason"] == "execution_entry_mismatch"
    assert client.post(f"/api/resources/{case.resource_id}/research-start-intent").status_code == 404


def test_pinned_promote_resolution_reuses_effective_state_and_exact_server_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy = _CatalogCase(
        "skill:pinned-healthy",
        ResourceAvailability.TRYABLE,
        _healthy_facts("pinned-healthy"),
        ResourceAvailability.TRYABLE,
        200,
    )
    gap = _CatalogCase(
        "skill:pinned-gap",
        ResourceAvailability.TRYABLE,
        ResourceCapabilityFacts(
            capability_id="pinned-gap",
            durable_executable=True,
            launch_mode="durable_skill",
            dependency_gaps=("missing dependency",),
            unavailable_reason=None,
            runtime_family="test",
        ),
        ResourceAvailability.CONFIGURATION_REQUIRED,
        404,
    )
    _install_catalog(monkeypatch, [healthy, gap])

    resolved = resource_catalog.get_resource_for_research_start(
        healthy.resource_id,
        "1.0.0",
        "pinned-healthy",
    )
    assert resolved is not None
    assert resolved["availability"] == "tryable"
    assert resource_catalog.get_resource_for_research_start(
        healthy.resource_id,
        "2.0.0",
        "pinned-healthy",
    ) is None
    assert resource_catalog.get_resource_for_research_start(
        healthy.resource_id,
        "1.0.0",
        "forged-capability",
    ) is None
    assert resource_catalog.get_resource_for_research_start(
        gap.resource_id,
        "1.0.0",
        "pinned-gap",
    ) is None
    assert resource_catalog.get_resource_for_research_start(
        healthy.resource_id,
        "1.0.0",
        None,
    ) is None


def test_public_contribution_without_execution_contract_remains_non_startable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contribution_id = "skill:community-public-resource"

    class _ContributionRepo:
        def list_public_catalog(self, *, limit: int, offset: int):
            if offset:
                return []
            return [
                {
                    "contribution_id": "contribution-1",
                    "catalog_resource_id": contribution_id,
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
                }
            ]

    monkeypatch.setattr(resource_catalog, "get_contribution_repository", lambda: _ContributionRepo())
    monkeypatch.setattr(resource_catalog, "_load_catalog_releases", lambda: {})
    monkeypatch.setattr(resource_catalog, "_project_skills", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_agents", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_workflows", lambda: [])
    resource_catalog.clear_resource_catalog_cache()

    listed = client.get("/api/resources?availability=configuration_required&pageSize=100")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    row = listed.json()["resources"][0]
    assert row["id"] == contribution_id
    assert row["availability"] == "configuration_required"
    assert row["executionBlocked"] is True
    assert row["executionEntry"] is None
    assert row["durableExecutable"] is False
    assert client.get(f"/api/resources/{contribution_id}").json() == row
    assert client.post(f"/api/resources/{contribution_id}/research-start-intent").status_code == 404


@pytest.mark.parametrize(
    ("resource_type", "execution_entry"),
    [
        (ResourceType.SKILL, "durable_agent"),
        (ResourceType.AGENT, "durable_skill"),
        (ResourceType.WORKFLOW, "durable_skill"),
    ],
)
def test_malformed_release_execution_entry_fails_closed(
    resource_type: ResourceType,
    execution_entry: str,
) -> None:
    case = _CatalogCase(
        f"{resource_type.value}:malformed-entry",
        ResourceAvailability.TRYABLE,
        _healthy_facts("malformed-entry"),
        ResourceAvailability.UNAVAILABLE,
        404,
        resource_type=resource_type,
        execution_entry=execution_entry,
    )
    with pytest.raises(resource_catalog.ResourceCatalogUnavailableError):
        _release(case)


def test_tryable_workflow_without_supported_execution_entry_fails_closed() -> None:
    case = _CatalogCase(
        "workflow:unsupported-start",
        ResourceAvailability.TRYABLE,
        _healthy_facts("unsupported-start"),
        ResourceAvailability.UNAVAILABLE,
        404,
        resource_type=ResourceType.WORKFLOW,
        execution_entry=None,
    )
    with pytest.raises(resource_catalog.ResourceCatalogUnavailableError):
        _release(case)


def test_tryable_detail_only_resource_remains_startable_from_same_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _CatalogCase(
        "skill:detail-only-start",
        ResourceAvailability.TRYABLE,
        _healthy_facts("detail-only-start"),
        ResourceAvailability.TRYABLE,
        200,
    )
    registry = _registry_resource(case)
    release = _release(case)
    release = replace(release, visible_in_catalog=False, visible_in_detail=True)
    monkeypatch.setattr(resource_catalog, "_project_skills", lambda: [registry])
    monkeypatch.setattr(resource_catalog, "_project_agents", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_workflows", lambda: [])
    monkeypatch.setattr(resource_catalog, "_project_approved_contributions", lambda: [])
    monkeypatch.setattr(resource_catalog, "_load_catalog_releases", lambda: {case.resource_id: release})
    monkeypatch.setattr(
        resource_catalog,
        "resolve_resource_capability_facts",
        lambda resource_id, resource_type: case.facts,
    )
    resource_catalog.clear_resource_catalog_cache()

    assert client.get("/api/resources?pageSize=100").json()["total"] == 0
    detail = client.get(f"/api/resources/{case.resource_id}")
    assert detail.status_code == 200
    assert detail.json()["availability"] == "tryable"
    assert client.post(f"/api/resources/{case.resource_id}/research-start-intent").status_code == 200

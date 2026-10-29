from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.models.resource_catalog import ResourceAvailability
from app.modules.intent_session_store import intent_session_store
from app.modules import resource_catalog
from app.modules.resource_capability_facts import ResourceCapabilityFacts


client = TestClient(app)

RELEASED_RESOURCE_IDS = {
    row["resourceId"]
    for row in yaml.safe_load(resource_catalog.CATALOG_RELEASES_PATH.read_text(encoding="utf-8"))["catalogReleases"]
}
EXPECTED_CATALOG_TOTAL = 133

def _catalog(*, query: str = "") -> dict[str, object]:
    response = client.get(f"/api/resources?page=1&pageSize=100{query}")
    assert response.status_code == 200
    payload = response.json()
    if payload["total"] > 100:
        page2 = client.get(f"/api/resources?page=2&pageSize=100{query}")
        assert page2.status_code == 200
        payload = {
            **payload,
            "resources": list(payload["resources"]) + list(page2.json()["resources"]),
        }
    return payload


def _release_payload() -> dict[str, object]:
    return yaml.safe_load(resource_catalog.CATALOG_RELEASES_PATH.read_text(encoding="utf-8"))


def _replace_release_data(monkeypatch: pytest.MonkeyPatch, tmp_path, payload: object) -> None:
    path = tmp_path / "catalog-releases.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(resource_catalog, "CATALOG_RELEASES_PATH", path)


def test_availability_contract_matches_fe02() -> None:
    assert {value.value for value in ResourceAvailability} == {
        "tryable", "configuration_required", "view_only", "unavailable",
    }


def test_catalog_projects_only_resources_with_public_catalog_releases() -> None:
    payload = _catalog()
    rows = payload["resources"]
    assert payload["total"] == EXPECTED_CATALOG_TOTAL
    assert {row["id"] for row in rows} == RELEASED_RESOURCE_IDS
    assert client.get("/api/resources?resourceType=agent").json()["total"] == 8
    assert client.get("/api/resources?resourceType=workflow").json()["total"] == 0
    validation_design = client.get("/api/resources/agent:validation_design").json()
    assert validation_design["visibleOnHome"] is True
    assert validation_design["availability"] == "tryable"
    assert validation_design["primaryAction"] == "try"
    assert validation_design["durableExecutable"] is True
    assert validation_design["executionBlocked"] is False
    assert client.get("/api/resources/skill:fixture_recorded_run_creation").status_code == 404

    released = client.get("/api/resources/skill:title_abstract_screening").json()
    assert released["releaseVersion"] == "1.0.0"
    assert released["licenseSummary"]
    assert released["evidenceStatus"] == "已声明验证事实"
    assert released["evidenceConflictDisposition"] is None
    assert released["keywords"] == []
    assert client.get("/api/resources/skill:fixture_recorded_run_creation").status_code == 404


def test_missing_release_data_fails_closed_without_registry_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(resource_catalog, "CATALOG_RELEASES_PATH", tmp_path / "missing.yaml")
    response = client.get("/api/resources")
    assert response.status_code == 503
    assert response.json()["detail"] == "resource_catalog_unavailable"


def test_search_type_availability_pagination_and_detail_share_one_projection() -> None:
    search = client.get("/api/resources?q=definitely-not-a-registry-resource&resourceType=skill&availability=configuration_required")
    assert search.status_code == 200
    assert search.json()["total"] == 0
    assert client.get("/api/resources?resourceType=agent").json()["total"] == 8
    assert client.get("/api/resources?resourceType=workflow").json()["total"] == 0

    first = client.get("/api/resources?page=1&pageSize=1").json()
    second = client.get("/api/resources?page=2&pageSize=1").json()
    assert first["total"] == second["total"] == EXPECTED_CATALOG_TOTAL
    assert len(first["resources"]) == len(second["resources"]) == 1
    assert {row["id"] for row in first["resources"]}.isdisjoint({row["id"] for row in second["resources"]})

    item = first["resources"][0]
    detail = client.get(f"/api/resources/{item['id']}")
    assert detail.status_code == 200
    assert detail.json() == item


def test_catalog_sort_matches_frontend_enum_and_precedes_pagination() -> None:
    ascending = client.get("/api/resources?sort=name_asc&page=1&pageSize=100")
    descending = client.get("/api/resources?sort=name_desc&page=1&pageSize=100")
    assert ascending.status_code == descending.status_code == 200
    ascending_rows = ascending.json()["resources"]
    descending_rows = descending.json()["resources"]
    assert [row["id"] for row in ascending_rows] == [
        row["id"] for row in sorted(ascending_rows, key=lambda row: (row["name"].casefold(), row["id"]))
    ]
    assert [row["id"] for row in descending_rows] == [
        row["id"] for row in sorted(descending_rows, key=lambda row: (row["name"].casefold(), row["id"]), reverse=True)
    ]
    first_page = client.get("/api/resources?sort=name_asc&page=1&pageSize=1").json()
    second_page = client.get("/api/resources?sort=name_asc&page=2&pageSize=1").json()
    assert first_page["resources"][0] == ascending_rows[0]
    assert second_page["resources"][0] == ascending_rows[1]
    assert client.get("/api/resources?sort=unknown").status_code == 422


def test_detail_visibility_is_independent_and_never_leaks_unreleased_registry_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    payload = _release_payload()
    payload["catalogReleases"][0]["visibleInCatalog"] = False
    payload["catalogReleases"][0]["visibleInDetail"] = False
    _replace_release_data(monkeypatch, tmp_path, payload)

    rows = _catalog()["resources"]
    assert payload["catalogReleases"][0]["resourceId"] not in {row["id"] for row in rows}
    assert client.get(f"/api/resources/{payload['catalogReleases'][0]['resourceId']}").status_code == 404


def test_zero_result_remains_successful_and_empty() -> None:
    response = client.get("/api/resources?q=definitely-not-a-registry-resource")
    assert response.status_code == 200
    assert response.json()["resources"] == []
    assert response.json()["total"] == 0


def test_visible_on_home_filter_returns_only_featured_tryable_rows() -> None:
    full = _catalog()
    featured = _catalog(query="&visibleOnHome=true")
    assert featured["total"] <= full["total"]
    assert featured["total"] > 0
    assert all(row["visibleOnHome"] is True for row in featured["resources"])
    assert all(row["availability"] == "tryable" for row in featured["resources"])
    assert all(row["executionBlocked"] is False for row in featured["resources"])
    expected = sum(
        1
        for row in full["resources"]
        if row["visibleOnHome"] is True
        and row["availability"] == "tryable"
        and row["executionBlocked"] is False
    )
    assert featured["total"] == expected


def test_featured_release_must_be_tryable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    payload = _release_payload()
    payload["catalogReleases"][0]["visibleOnHome"] = True
    payload["catalogReleases"][0]["availability"] = "view_only"
    payload["catalogReleases"][0]["executionBlocked"] = True
    payload["catalogReleases"][0]["primaryAction"] = "view"
    payload["catalogReleases"][0]["executionEntry"] = None
    _replace_release_data(monkeypatch, tmp_path, payload)
    resource_catalog.clear_resource_catalog_cache()
    response = client.get("/api/resources")
    assert response.status_code == 503
    assert response.json()["detail"] == "resource_catalog_unavailable"


@pytest.mark.parametrize("case", ["duplicate", "unknown", "corrupt"])
def test_invalid_present_release_data_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path, case: str) -> None:
    if case == "corrupt":
        path = tmp_path / "catalog-releases.yaml"
        path.write_text("catalogReleases: [", encoding="utf-8")
        monkeypatch.setattr(resource_catalog, "CATALOG_RELEASES_PATH", path)
    else:
        payload = _release_payload()
        releases = payload["catalogReleases"]
        if case == "duplicate":
            releases.append(dict(releases[0]))
        else:
            releases[0]["resourceId"] = "skill:not_registered"
        _replace_release_data(monkeypatch, tmp_path, payload)
    response = client.get("/api/resources")
    assert response.status_code == 503
    assert response.json()["detail"] == "resource_catalog_unavailable"


def test_invalid_schema_and_state_conflict_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    schema_path = tmp_path / "catalog-release.schema.json"
    schema_path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(resource_catalog, "CATALOG_RELEASE_SCHEMA_PATH", schema_path)
    assert client.get("/api/resources").status_code == 503

    payload = _release_payload()
    # Force an availability/action conflict while keeping schema-valid enums.
    payload["catalogReleases"][0]["availability"] = "view_only"
    payload["catalogReleases"][0]["executionBlocked"] = False
    payload["catalogReleases"][0]["primaryAction"] = "try"
    payload["catalogReleases"][0]["executionEntry"] = "durable_skill"
    _replace_release_data(monkeypatch, tmp_path, payload)
    monkeypatch.setattr(resource_catalog, "CATALOG_RELEASE_SCHEMA_PATH", resource_catalog.repo_root() / "schemas" / "resources" / "catalog-release.schema.json")
    response = client.get("/api/resources")
    assert response.status_code == 503
    assert response.json()["detail"] == "resource_catalog_unavailable"


@pytest.mark.parametrize(
    ("resource_id", "execution_entry"),
    [
        ("skill:ablation_design", "durable_agent"),
        ("agent:general_research_agent", "durable_skill"),
        ("skill:ablation_design", None),
    ],
)
def test_malformed_or_cross_type_execution_entry_fails_closed_at_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    resource_id: str,
    execution_entry: str | None,
) -> None:
    payload = _release_payload()
    release = next(item for item in payload["catalogReleases"] if item["resourceId"] == resource_id)
    release["executionEntry"] = execution_entry
    _replace_release_data(monkeypatch, tmp_path, payload)

    response = client.get("/api/resources")
    assert response.status_code == 503
    assert response.json()["detail"] == "resource_catalog_unavailable"


@pytest.mark.parametrize("projection", ["_project_skills", "_project_agents", "_project_workflows"])
def test_registry_failure_returns_503_not_empty_success(monkeypatch: pytest.MonkeyPatch, projection: str) -> None:
    def fail() -> list[resource_catalog.CatalogResource]:
        raise resource_catalog.ResourceCatalogUnavailableError("unavailable")

    resource_catalog.clear_resource_catalog_cache()
    monkeypatch.setattr(resource_catalog, projection, fail)
    response = client.get("/api/resources")
    assert response.status_code == 503
    assert response.json()["detail"] == "resource_catalog_unavailable"


def test_catalog_has_no_write_route() -> None:
    assert client.post("/api/resources").status_code == 405


def test_catalog_research_intent_resolves_and_pins_server_resource_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    resource_id = "skill:title_abstract_screening"
    capability_id = "title_abstract_screening"
    release_payload = _release_payload()
    release = next(item for item in release_payload["catalogReleases"] if item["resourceId"] == resource_id)
    release.update({"availability": "tryable", "executionBlocked": False, "primaryAction": "try", "executionEntry": "durable_skill"})
    _replace_release_data(monkeypatch, tmp_path, release_payload)

    def available_facts(candidate_id: str, resource_type: str) -> ResourceCapabilityFacts:
        if candidate_id == resource_id and resource_type == "skill":
            return ResourceCapabilityFacts(
                capability_id=capability_id,
                durable_executable=True,
                launch_mode="durable_skill",
                dependency_gaps=(),
                unavailable_reason=None,
                runtime_family="skill",
            )
        return ResourceCapabilityFacts(
            capability_id=candidate_id.removeprefix(f"{resource_type}:"),
            durable_executable=False,
            launch_mode=None,
            dependency_gaps=(),
            unavailable_reason="unavailable",
            runtime_family=resource_type,
        )

    monkeypatch.setattr(resource_catalog, "resolve_resource_capability_facts", available_facts)

    forged = client.post(
        f"/api/resources/{resource_id}/research-start-intent",
        json={"capabilityId": "fixture_recorded_run_creation", "resourceVersion": "forged"},
    )
    assert forged.status_code == 422

    response = client.post(f"/api/resources/{resource_id}/research-start-intent")
    assert response.status_code == 200
    payload = response.json()
    assert payload["researchStartUrl"] == f"/?intent=create&sessionId={payload['intentId']}"

    session = intent_session_store.get(payload["intentId"])
    assert session is not None
    assert session["selectedResourceId"] == resource_id
    assert session["selectedResourceVersion"] == "1.0.0"
    assert session["selectedCapabilityId"] == capability_id

    repeated = client.post(f"/api/resources/{resource_id}/research-start-intent")
    assert repeated.status_code == 200
    repeated_payload = repeated.json()
    assert repeated_payload["intentId"] != payload["intentId"]
    assert repeated_payload["researchStartUrl"] == (
        f"/?intent=create&sessionId={repeated_payload['intentId']}"
    )
    repeated_session = intent_session_store.get(repeated_payload["intentId"])
    assert repeated_session is not None
    assert repeated_session["selectedResourceId"] == resource_id
    assert repeated_session["selectedResourceVersion"] == "1.0.0"
    assert repeated_session["selectedCapabilityId"] == capability_id


def test_catalog_research_intent_rejects_unavailable_public_resource() -> None:
    response = client.post("/api/resources/skill:title_abstract_screening/research-start-intent")
    assert response.status_code == 404

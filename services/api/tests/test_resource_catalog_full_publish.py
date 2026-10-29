"""Full official CatalogRelease integrity checks for Resource Plaza."""

from __future__ import annotations

from collections import Counter
import re

import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.modules import resource_catalog
from app.modules.agent_registry import load_agent_registry
from app.modules.resource_capability_facts import resolve_resource_capability_facts
from app.modules.resource_catalog import NON_PRODUCT_SKILL_IDS, _load_skill_entries

client = TestClient(app)

ALLOWED_DOMAINS = {"cs_research", "general", "materials", "ope", "opentrajectory", "sdr"}
ALLOWED_SCENARIOS = {
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
}


def _release_rows() -> list[dict]:
    payload = yaml.safe_load(resource_catalog.CATALOG_RELEASES_PATH.read_text(encoding="utf-8"))
    return list(payload["catalogReleases"])


def _target_ids() -> set[str]:
    skills = {
        f"skill:{entry['skill_id']}"
        for entry in _load_skill_entries()
        if isinstance(entry.get("skill_id"), str)
        and entry["skill_id"].strip()
        and entry["skill_id"].strip() not in NON_PRODUCT_SKILL_IDS
    }
    agents = {
        f"agent:{agent.agentId}"
        for agent in load_agent_registry(validate=False).list_agents()
        if agent.enabled
    }
    return skills | agents


def test_full_catalog_release_count_and_identity_set() -> None:
    rows = _release_rows()
    ids = [row["resourceId"] for row in rows]
    target = _target_ids()
    assert len(rows) == 133
    assert len(ids) == len(set(ids)) == 133
    assert set(ids) == target
    assert "skill:fixture_recorded_run_creation" not in set(ids)
    pairs = [(row["resourceId"], str(row["releaseVersion"])) for row in rows]
    assert len(pairs) == len(set(pairs))


def test_full_catalog_classification_and_availability_contract() -> None:
    rows = _release_rows()
    for row in rows:
        assert row["domains"] and set(row["domains"]) <= ALLOWED_DOMAINS
        assert row["scenarios"] and set(row["scenarios"]) <= ALLOWED_SCENARIOS
        resource_type = row["resourceType"]
        facts = resolve_resource_capability_facts(row["resourceId"], resource_type)
        if row["availability"] == "tryable":
            assert row["executionBlocked"] is False
            assert row["primaryAction"] == "try"
            assert row["executionEntry"]
            assert facts.durable_executable is True
            assert list(facts.dependency_gaps) == []
            assert facts.unavailable_reason is None
        elif row["availability"] == "view_only":
            assert row["executionBlocked"] is True
            assert row["primaryAction"] == "view"
            assert row["executionEntry"] is None
            # Catalog may keep a durable capability view-only until an execution
            # entry is published. Runtime facts can still project as executable;
            # research-start gates on release.availability + executionEntry.
        else:
            raise AssertionError(f"unexpected availability: {row['availability']}")


def test_public_catalog_exposes_all_releases_and_filters() -> None:
    payload = client.get("/api/resources?page=1&pageSize=100").json()
    assert payload["total"] == 133
    assert len(payload["resources"]) == 100
    page2 = client.get("/api/resources?page=2&pageSize=100").json()
    assert page2["total"] == 133
    assert len(page2["resources"]) == 33
    ids = {row["id"] for row in payload["resources"]} | {row["id"] for row in page2["resources"]}
    assert len(ids) == 133
    assert "skill:fixture_recorded_run_creation" not in ids

    skills = client.get("/api/resources?resourceType=skill&pageSize=100").json()
    agents = client.get("/api/resources?resourceType=agent&pageSize=100").json()
    assert skills["total"] == 125
    assert agents["total"] == 8

    domain = client.get("/api/resources?domain=cs_research&pageSize=100").json()
    scenario = client.get("/api/resources?scenario=problem_modeling&pageSize=100").json()
    assert domain["total"] > 0
    assert scenario["total"] > 0
    assert all("cs_research" in row["domains"] for row in domain["resources"])
    assert all("problem_modeling" in row["scenarios"] for row in scenario["resources"])


def test_public_catalog_uses_chinese_research_presentation() -> None:
    first_page = client.get("/api/resources?page=1&pageSize=100").json()
    second_page = client.get("/api/resources?page=2&pageSize=100").json()
    resources = [*first_page["resources"], *second_page["resources"]]

    assert len(resources) == 133
    for resource in resources:
        assert not re.search(r"[A-Za-z]{2,}", resource["name"]), resource["id"]
        assert not re.search(r"[A-Za-z]{2,}", resource["description"]), resource["id"]


def test_view_only_cannot_start_research_and_tryable_can() -> None:
    rows = _release_rows()
    view_only = next(row for row in rows if row["availability"] == "view_only")
    tryable = next(row for row in rows if row["availability"] == "tryable")
    blocked = client.post(f"/api/resources/{view_only['resourceId']}/research-start-intent")
    assert blocked.status_code == 404
    allowed = client.post(f"/api/resources/{tryable['resourceId']}/research-start-intent")
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["researchStartUrl"].startswith("/?intent=create&sessionId=")


def test_detail_matches_list_projection_for_sampled_resources() -> None:
    page = client.get("/api/resources?sort=name_asc&page=1&pageSize=5").json()
    for row in page["resources"]:
        detail = client.get(f"/api/resources/{row['id']}")
        assert detail.status_code == 200
        assert detail.json() == row
        assert row["domains"]
        assert row["scenarios"]


def test_availability_histogram_is_stable() -> None:
    rows = _release_rows()
    counts = Counter(row["availability"] for row in rows)
    assert set(counts) <= {"tryable", "view_only", "configuration_required", "unavailable"}
    assert counts["tryable"] + counts["view_only"] == 133

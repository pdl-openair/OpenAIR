from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from app.execution.plan_resource_relation import (
    enforce_readiness_relation,
    verify_plan_resource_relation,
)


def _case(plan_commit: str = "a", actual_commit: str = "a") -> tuple[dict, dict]:
    repository = "https://example.test/official.git"
    plan = {"source_identity": {
        "repository": repository, "resolved_commit_sha": plan_commit * 40,
    }}
    acquisition = {
        "state": "succeeded",
        "repository": repository,
        "requested_revision": actual_commit * 40,
        "resolved_commit_sha": actual_commit * 40,
        "verified_head": actual_commit * 40,
        "workspace_relative_path": "repository",
        "execution_evidence": {
            "git_version": "git version test",
            "head_matches_resolved_commit": True,
            "operations": [
                {"operation": operation, "exit_code": 0}
                for operation in ("clone", "resolve_revision", "checkout", "read_head")
            ],
        },
    }
    return plan, acquisition


def test_formal_plan_and_trusted_actual_match_allows_ready():
    plan, actual = _case()
    assert verify_plan_resource_relation(plan, actual) == "verified"
    result = enforce_readiness_relation(
        {"status": "ready", "blocking_gaps": [], "executable_scope": ["main"]},
        plan, actual,
    )
    assert result["status"] == "ready"
    assert result["executable_scope"] == ["main"]
    assert result["blocking_gaps"] == []
    assert result["plan_resource_relation_status"] == "verified"


@pytest.mark.parametrize("model_status", ["not_ready", "partially_ready", "ready"])
def test_unverified_relation_always_records_a_recoverable_readiness_gap(model_status: str):
    plan, acquisition = _case()
    del acquisition["execution_evidence"]
    output = {"status": model_status, "executable_scope": ["main"], "blocking_gaps": []}

    result = enforce_readiness_relation(output, plan, acquisition)

    assert result["plan_resource_relation_status"] == "not_verified"
    assert result["status"] == "not_ready"
    assert result["executable_scope"] == []
    assert result["blocking_gaps"] == [
        {"code": "plan_resource_relation", "status": "not_verified"}
    ]
    assert output == {"status": model_status, "executable_scope": ["main"], "blocking_gaps": []}


def test_mismatch_downgrades_partial_readiness_and_preserves_other_findings():
    plan, acquisition = _case("a", "b")
    existing_gap = {"code": "environment_unavailable"}
    output = {
        "status": "partially_ready", "executable_scope": ["main"],
        "blocking_gaps": [existing_gap],
        "code": {"acquisition_evidence": "retained"},
        "resources": {"research_result": "retained"},
        "environment": {"evidence": "retained"},
        "entry_verification": {"judgment": "retained"},
        "unknowns": ["further evidence needed"],
    }

    result = enforce_readiness_relation(output, plan, acquisition)

    assert result["plan_resource_relation_status"] == "mismatch"
    assert result["status"] == "not_ready"
    assert result["executable_scope"] == []
    assert result["blocking_gaps"] == [
        existing_gap, {"code": "plan_resource_relation", "status": "mismatch"}
    ]
    for field in ("code", "resources", "environment", "entry_verification", "unknowns"):
        assert result[field] == output[field]
    assert result.get("state") != "failed"
    root = Path(__file__).resolve().parents[3]
    schema = json.loads((
        root / "skills" / "cs_research" / "execution-readiness-assessment" / "output.schema.json"
    ).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(result)


def test_existing_relation_gap_is_not_duplicated():
    plan, acquisition = _case()
    del acquisition["execution_evidence"]
    gap = {"code": "plan_resource_relation", "status": "not_verified"}
    result = enforce_readiness_relation(
        {"status": "not_ready", "executable_scope": [], "blocking_gaps": [gap]},
        plan, acquisition,
    )
    assert result["blocking_gaps"] == [gap]


@pytest.mark.parametrize("design_commit", ["a", "b"])
def test_experiment_design_copy_has_no_authority(design_commit: str):
    plan, actual = _case("a", "b")
    design = {"repository": actual["repository"], "revision": design_commit * 40}
    assert design["revision"] in {"a" * 40, "b" * 40}
    assert verify_plan_resource_relation(plan, actual) == "mismatch"
    result = enforce_readiness_relation(
        {"status": "ready", "blocking_gaps": [], "executable_scope": ["main"]},
        plan, actual,
    )
    assert result["status"] == "not_ready"
    assert result["plan_resource_relation_status"] == "mismatch"


def test_design_disagrees_but_formal_plan_matches_actual():
    plan, actual = _case("b", "b")
    design = {"revision": "a" * 40}
    assert design["revision"] != plan["source_identity"]["resolved_commit_sha"]
    assert verify_plan_resource_relation(plan, actual) == "verified"


@pytest.mark.parametrize("mutation", [
    "missing_execution_evidence", "head_check_failed", "missing_verified_head",
    "failed_head_operation", "resolved_head_mismatch",
])
def test_each_missing_trust_signal_is_not_verified(mutation: str):
    plan, acquisition = _case()
    acquisition = deepcopy(acquisition)
    if mutation == "missing_execution_evidence":
        del acquisition["execution_evidence"]
    elif mutation == "head_check_failed":
        acquisition["execution_evidence"]["head_matches_resolved_commit"] = False
    elif mutation == "missing_verified_head":
        del acquisition["verified_head"]
    elif mutation == "failed_head_operation":
        acquisition["execution_evidence"]["operations"][-1]["exit_code"] = 1
    else:
        acquisition["verified_head"] = "b" * 40
    assert verify_plan_resource_relation(plan, acquisition) == "not_verified"


def test_correct_requested_revision_and_success_status_cannot_hide_mismatch():
    plan, acquisition = _case("a", "b")
    acquisition["requested_revision"] = "a" * 40
    assert verify_plan_resource_relation(plan, acquisition) == "mismatch"
    assert enforce_readiness_relation(
        {"status": "ready", "blocking_gaps": [], "executable_scope": ["main"]},
        plan, acquisition,
    )["status"] == "not_ready"


def test_all_resumable_binding_versions_read_formal_plan():
    root = Path(__file__).resolve().parents[3]
    rows = yaml.safe_load((root / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    plan_binding = (
        "bind:cs_reproduction:reproduction_experiment_design:skill:execution_plan_fact_assembly"
    )
    prefix = "bind:cs_reproduction:execution_readiness_validation:skill:"
    for version in (1, 2, 3):
        for skill, expected in (
            ("repository_revision_acquisition", {
                "repository": "source_identity.repository",
                "revision": "source_identity.resolved_commit_sha",
            }),
            ("resource_identity_acquisition", {"resources": "resource_acquisition_requests"}),
            ("execution_readiness_assessment", {"execution_plan_facts": "$"}),
        ):
            row = next(item for item in rows if item["bindingId"] == prefix + skill
                       and item["bindingVersion"] == version)
            fields = {item["targetField"]: item for item in row["inputSources"]["fields"]}
            for target, field in expected.items():
                assert fields[target]["sourcePresence"] == "required"
                assert fields[target]["source"] == {
                    "kind": "artifact", "bindingId": plan_binding,
                    "bindingVersion": version, "field": field,
                }

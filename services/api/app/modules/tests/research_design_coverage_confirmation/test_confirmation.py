"""Confirmation permits executable target gaps without asserting completion."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from app.models.research_orchestration import ResearchDesignVersion
from app.modules.research_completion_decision import expected_project_completion_evaluation
from app.modules.research_design_coverage_confirmation import coverage_allows_confirmation
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[6]


def gap_design():
    payload = json.loads(
        (ROOT / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json").read_text(
            encoding="utf-8"
        )
    )["researchTask"]["researchDesignVersions"][0]
    payload["understanding"]["targetOutputs"].append("Unassociated software control")
    payload["projectCompletionRequirements"].append(
        {
            "completionRequirementId": "gap",
            "targetOutputRef": "understanding.targetOutputs[1]",
            "description": "Unassociated software control",
        }
    )
    report = payload["coverageReport"]
    target = deepcopy(report["targets"][0])
    target.update(targetId="gap", sourcePath="understanding.targetOutputs[1]")
    report["targets"].append(target)
    claim = deepcopy(report["claims"][0])
    claim.update(targetId="gap", instanceRefs=[], acceptanceCriterionRefs=[], coverageMode="none")
    report["claims"].append(claim)
    report.update(status="executable_with_gaps", uncoveredRequiredTargetIds=["gap"])
    return payload


def test_confirmable_gap_is_retained_and_cannot_complete():
    payload = gap_design()
    design = ResearchDesignVersion.model_validate(payload)
    assert coverage_allows_confirmation(design.coverage_report)
    assert design.understanding.target_outputs[-1] == "Unassociated software control"
    state = {"researchDesignVersion": design.version, "executionState": {}, "scientificFacts": {}}
    result = expected_project_completion_evaluation(design, state)
    assert not result["canComplete"]
    assert result["requirementAssessments"][-1]["status"] == "unresolved"


@pytest.mark.parametrize(
    "mutation", ["guard", "overload", "dependency", "duplicate", "missing_claim"]
)
def test_new_status_cannot_disguise_non_target_or_invalid_coverage(mutation):
    payload = gap_design()
    report = payload["coverageReport"]
    if mutation == "guard":
        report["targets"][-1]["sourceType"] = "constraint"
    elif mutation == "overload":
        report["overloadedInstanceRefs"] = report["claims"][0]["instanceRefs"]
    elif mutation == "dependency":
        report["missingDependencyRefs"] = ["invalid dependency"]
    elif mutation == "duplicate":
        report["claims"].append(deepcopy(report["claims"][-1]))
    else:
        report["claims"].pop()
    assert not coverage_allows_confirmation(report)
    with pytest.raises(ValidationError):
        ResearchDesignVersion.model_validate(payload)


@pytest.mark.parametrize(
    "status,allowed",
    [
        ("passed_structural", True),
        ("passed_semantic", True),
        ("needs_revision", False),
    ],
)
def test_legacy_status_contract(status, allowed):
    assert coverage_allows_confirmation({"status": status}) is allowed


def test_invalid_instance_reference_still_rejected():
    payload = gap_design()
    payload["coverageReport"]["claims"][0]["instanceRefs"][0]["substageInstanceId"] = "absent"
    with pytest.raises(ValidationError, match="unknown instance"):
        ResearchDesignVersion.model_validate(payload)


def test_full_boundary_claim_requires_execution_and_acceptance_references():
    payload = gap_design()
    report = payload["coverageReport"]
    report["targets"][0]["sourceType"] = "constraint"
    report["claims"][0]["instanceRefs"] = []
    report["claims"][0]["acceptanceCriterionRefs"] = []
    assert not coverage_allows_confirmation(report)
    with pytest.raises(ValidationError):
        ResearchDesignVersion.model_validate(payload)

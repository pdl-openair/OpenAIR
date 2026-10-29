"""Controlled state inputs exercise the sole production completion consumer."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from app.models.research_orchestration import ResearchDesignVersion
from app.modules.research_completion_decision import expected_project_completion_evaluation
from app.modules.target_output_completion import validate_reuse_acceptance_projection
from app.modules.target_output_contributions import combine_contribution_status
from app.modules.target_output_ownership import revision_target_metadata, validate_target_owners
from pydantic import ValidationError

TARGET = "understanding.targetOutputs[0]"
ROOT = Path(__file__).resolve().parents[6]


@pytest.mark.parametrize("status", ["unknown", "unresolved", "", None])
def test_invalid_contribution_status_never_defaults_to_success(status):
    assert combine_contribution_status("satisfied", [{"status": status}]) == "unresolved"


def inputs(modes):
    keys = [f"contribution_{index}" for index in range(len(modes))]
    requirement = {
        "completionRequirementId": "combined",
        "targetOutputRef": TARGET,
        "description": "Software control",
        "contributingResponsibilityKeys": keys,
    }
    coverage = [
        {
            "responsibilityKey": key,
            "coverageMode": mode,
            "requirement": "conditional",
            "reusedResultRefs": [f"source:{key}"],
        }
        for key, mode in zip(keys, modes, strict=True)
    ]
    criteria = [
        f"criterion:{key}" for key, mode in zip(keys, modes, strict=True) if mode == "planned_work"
    ]
    design = {
        "researchDesignId": "control",
        "version": 1,
        "projectCompletionRequirements": [requirement],
        "workflowResponsibilityCoverage": coverage,
        "coverageReport": {
            "targets": [
                {"targetId": "target", "sourceType": "target_output", "sourcePath": TARGET}
            ],
            "claims": [
                {"targetId": "target", "coverageMode": "full", "acceptanceCriterionRefs": criteria}
            ],
        },
    }
    state = {
        "researchDesignVersion": 1,
        "sourceRefs": ["artifact:acceptance"],
        "scientificFacts": {"targetOutputReuseAcceptances": []},
        "executionState": {
            "validatedArtifactRefs": ["artifact:acceptance"],
            "approvedArtifactRefs": ["artifact:acceptance"],
            "substageCompletion": [
                {"criteria": [{"criterionId": key, "status": "satisfied"} for key in criteria]}
            ],
        },
    }
    return design, state


def accept(design, state, key):
    row = next(
        row for row in design["workflowResponsibilityCoverage"] if row["responsibilityKey"] == key
    )
    acceptance = {
        "researchDesignId": "control",
        "researchDesignVersion": 1,
        "targetOutputRef": TARGET,
        "responsibilityKey": key,
        "reusedResultRefs": row["reusedResultRefs"],
        "acceptanceArtifactRef": "artifact:acceptance",
        "verdict": "accepted",
    }
    state["scientificFacts"]["targetOutputReuseAcceptances"].append(acceptance)
    return acceptance


@pytest.mark.parametrize(
    "modes",
    [
        ["planned_work", "reused_result"],
        ["reused_result", "reused_result"],
        ["planned_work", "not_selected"],
        ["planned_work", "planned_work"],
        ["reused_result", "not_selected"],
        ["not_selected", "not_selected"],
    ],
)
def test_all_necessary_contributions_must_be_satisfied(modes):
    design, state = inputs(modes)
    for row in design["workflowResponsibilityCoverage"]:
        if row["coverageMode"] == "reused_result":
            assert not expected_project_completion_evaluation(design, state)["canComplete"]
            accept(design, state, row["responsibilityKey"])
    assert expected_project_completion_evaluation(design, state)["canComplete"]
    criteria = state["executionState"]["substageCompletion"][0]["criteria"]
    for criterion in criteria:
        original = deepcopy(criterion)
        criterion["status"] = "unmet"
        assert not expected_project_completion_evaluation(design, state)["canComplete"]
        criterion.update(original)


@pytest.mark.parametrize(
    "field,value",
    [
        ("researchDesignVersion", 2),
        ("researchDesignId", "old"),
        ("responsibilityKey", "other"),
        ("targetOutputRef", "other"),
        ("reusedResultRefs", ["wrong"]),
        ("acceptanceArtifactRef", "wrong"),
    ],
)
def test_reuse_scope_or_source_mismatch_cannot_complete(field, value):
    design, state = inputs(["planned_work", "reused_result"])
    row = accept(design, state, "contribution_1")
    row[field] = value
    assert not expected_project_completion_evaluation(design, state)["canComplete"]


@pytest.mark.parametrize("key", ["approvedArtifactRefs", "validatedArtifactRefs"])
def test_unaccepted_artifact_cannot_complete(key):
    design, state = inputs(["planned_work", "reused_result"])
    accept(design, state, "contribution_1")
    state["executionState"][key] = []
    assert not expected_project_completion_evaluation(design, state)["canComplete"]


def test_projection_accepts_each_declared_reuse_contributor_only():
    design, state = inputs(["reused_result", "reused_result"])
    for key in ["contribution_0", "contribution_1"]:
        accept(design, state, key)
    validate_reuse_acceptance_projection(
        state["scientificFacts"],
        {"researchDesignVersions": [design]},
        "artifact:acceptance",
        1,
    )
    state["scientificFacts"]["targetOutputReuseAcceptances"][0]["responsibilityKey"] = "other"
    with pytest.raises(ValueError):
        validate_reuse_acceptance_projection(
            state["scientificFacts"],
            {"researchDesignVersions": [design]},
            "artifact:acceptance",
            1,
        )


def formal_payload():
    return json.loads(
        (ROOT / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json").read_text(
            encoding="utf-8"
        )
    )["researchDesignCompositeContributions"]


def test_formal_composite_and_revision_preserve_contributors():
    design = ResearchDesignVersion.model_validate(formal_payload())
    metadata = revision_target_metadata(
        design,
        {
            row.substage_instance_id: f"revision_{index}"
            for index, row in enumerate(design.instances)
        },
    )
    assert metadata["targetOutputContributors"] == {TARGET: ["current", "reuse"]}
    assert design.project_completion_requirements[0].workflow_responsibility_key is None


@pytest.mark.parametrize(
    "mapping",
    [
        {"absent": ["current"]},
        {TARGET: ["unknown"]},
        {TARGET: []},
        {TARGET: ["current", "current"]},
    ],
)
def test_proposal_contribution_metadata_cannot_be_silently_dropped(mapping):
    design = ResearchDesignVersion.model_validate(formal_payload())
    with pytest.raises(ValueError):
        validate_target_owners(
            {},
            design.understanding.target_outputs,
            design.workflow_responsibility_coverage,
            mapping,
        )


@pytest.mark.parametrize(
    "mutation", ["unknown", "duplicate", "owner", "missing_current", "missing_criterion"]
)
def test_formal_composite_rejects_invalid_identity_or_missing_current_acceptance(mutation):
    payload = formal_payload()
    req = payload["projectCompletionRequirements"][0]
    claim = payload["coverageReport"]["claims"][0]
    if mutation == "unknown":
        req["contributingResponsibilityKeys"].append("absent")
    elif mutation == "duplicate":
        req["contributingResponsibilityKeys"].append("current")
    elif mutation == "owner":
        req["workflowResponsibilityKey"] = "current"
    elif mutation == "missing_current":
        claim["instanceRefs"] = []
    else:
        claim["acceptanceCriterionRefs"] = []
    with pytest.raises(ValidationError):
        ResearchDesignVersion.model_validate(payload)

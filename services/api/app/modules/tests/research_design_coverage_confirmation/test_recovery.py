"""Recovery must preserve executable target gaps without bypassing structure."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from app.models.research_orchestration import (
    ResearchDesignRef,
    ResearchDesignVersion,
    StageKey,
)
from app.modules.research_design_coverage_confirmation import coverage_allows_confirmation
from app.modules.research_design_recovery import (
    _recovery_coverage,
    build_semantic_gap_recovery_draft,
)

from .test_confirmation import gap_design

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def recover(active):
    return build_semantic_gap_recovery_draft(
        active=active,
        blocked=active.instances[0],
        recovery_id="software_recovery",
        recovery_definition_id="software:recovery",
        recovery_definition_version=1,
        recovery_stage_key=StageKey.PROBLEM_MODELING,
        recovery_objective="Offline contract audit input",
        recovery_artifact_types=["evidence_summary"],
        created_at=NOW,
    )


def test_recovery_preserves_target_gaps_and_rechecks_valid_formal_draft():
    active = ResearchDesignVersion.model_validate(gap_design())
    draft = recover(active)
    checked = ResearchDesignVersion.model_validate(draft.model_dump(mode="json", by_alias=True))
    assert checked.coverage_report.status == "executable_with_gaps"
    assert coverage_allows_confirmation(checked.coverage_report)
    assert (
        checked.coverage_report.uncovered_required_target_ids
        == active.coverage_report.uncovered_required_target_ids
    )
    assert checked.understanding.target_outputs == active.understanding.target_outputs
    assert checked.status == "draft"
    assert checked.confirmation_ref is None
    assert checked.version == active.version + 1


def test_recovery_overload_cannot_be_confirmed():
    payload = gap_design()
    payload["scopeBoundary"]["maxSubstageInstances"] = 1
    draft = recover(ResearchDesignVersion.model_validate(payload))
    assert draft.coverage_report.overloaded_instance_refs
    assert draft.coverage_report.status == "needs_revision"
    assert not coverage_allows_confirmation(draft.coverage_report)


def test_recovery_rechecks_missing_dependencies():
    active = ResearchDesignVersion.model_validate(gap_design())
    draft = recover(active)
    # Feed the rebuilt producer a graph missing the actual recovery dependency.
    remaining = [row for row in draft.instances if row.substage_instance_id != "software_recovery"]
    report = _recovery_coverage(
        active=active,
        next_design_ref=ResearchDesignRef(
            researchDesignId=active.research_design_id,
            version=active.version + 1,
        ),
        instances=remaining,
        revised_refs={},
        created_at=NOW,
    )
    assert report.missing_dependency_refs
    assert report.status == "needs_revision"
    assert not coverage_allows_confirmation(report)


@pytest.mark.parametrize("status", ["passed_structural", "passed_semantic"])
def test_recovery_rechecks_legacy_pass_without_inheriting_semantic_approval(status):
    payload = gap_design()
    payload["understanding"]["targetOutputs"].pop()
    payload["projectCompletionRequirements"].pop()
    report = payload["coverageReport"]
    report["targets"].pop()
    report["claims"].pop()
    report.update(status=status, uncoveredRequiredTargetIds=[])
    draft = recover(ResearchDesignVersion.model_validate(payload))
    assert draft.coverage_report.status == "passed_structural"
    assert draft.coverage_report.semantic_evaluation_ref is None


def test_workflow_recovery_rebases_responsibilities_without_changing_ownership():
    payload = gap_design()
    original = payload["instances"][0]
    dependent = deepcopy(original)
    dependent.update(substageInstanceId="dependent", sequenceHint=2)
    dependent["dependencies"] = [
        {
            "dependencyId": "dependency_on_original",
            "upstreamInstanceRef": {
                "substageInstanceId": original["substageInstanceId"],
                "instanceRevision": 1,
            },
            "conditionType": "artifact_usable",
            "requiredArtifactTypes": ["evidence_summary"],
            "confirmationTargetRef": None,
        }
    ]
    independent = deepcopy(original)
    independent.update(substageInstanceId="independent", sequenceHint=3)
    payload["instances"].extend([dependent, independent])
    payload["scopeBoundary"]["maxSubstageInstances"] = 4
    payload["workflowProvenanceRef"] = {
        "workflowId": "software_workflow",
        "workflowVersion": 1,
        "contentHash": "sha256:" + "a" * 64,
    }
    payload["workflowResponsibilityCoverage"] = [
        {
            "responsibilityKey": "responsibility_" + row["substageInstanceId"],
            "definitionId": row["substageDefinitionId"],
            "requirement": "core",
            "coverageMode": "planned_work",
            "instanceRefs": [
                {"substageInstanceId": row["substageInstanceId"], "instanceRevision": 1}
            ],
            "reusedResultRefs": [],
            "rationale": "Frozen software contract control",
        }
        for row in payload["instances"]
    ]
    active = ResearchDesignVersion.model_validate(payload)
    before = active.model_dump(mode="json", by_alias=True)
    draft = recover(active)
    checked = ResearchDesignVersion.model_validate(draft.model_dump(mode="json", by_alias=True))
    assert active.model_dump(mode="json", by_alias=True) == before
    refs = {row.substage_instance_id: row.instance_revision for row in checked.instances}
    assert refs[original["substageInstanceId"]] == refs["dependent"] == 2
    assert refs["independent"] == 1
    for old, new in zip(
        active.workflow_responsibility_coverage,
        checked.workflow_responsibility_coverage,
        strict=True,
    ):
        assert old.model_dump(exclude={"instance_refs"}) == new.model_dump(
            exclude={"instance_refs"}
        )
        assert all(
            ref.instance_revision == refs[ref.substage_instance_id] for ref in new.instance_refs
        )
    assert checked.status == "draft" and checked.confirmation_ref is None
    assert checked.understanding == active.understanding
    assert checked.project_completion_requirements == active.project_completion_requirements

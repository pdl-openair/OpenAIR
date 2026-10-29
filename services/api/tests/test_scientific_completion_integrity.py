from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.research_orchestration import (
    READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY,
    ResearchTaskV12,
)
from app.modules.research_execution_decision_service import (
    DecisionPolicyViolation,
    reconcile_task_lifecycle_from_runtime,
)
from app.modules.research_finalization import finalize_from_validated_facts
from app.modules.task_public_projection import (
    project_public_task,
    project_public_task_read_only,
)


NOW = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _payload(
    *,
    runtime_status: str = "completed",
    artifact_type: str = "evidence_summary",
    artifact_state: str = "usable",
) -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    payload["status"] = "running"
    payload["substageRuntimeStates"][0].update({
        "status": runtime_status,
        "statusReasonCode": (
            None if runtime_status == "completed" else f"test_{runtime_status}"
        ),
        "activeExecutionDecisionId": None,
        "updatedAt": NOW.isoformat(),
    })
    payload["workUnits"][0].update({
        "status": "completed",
        "terminalReasonCode": None,
        "finishedAt": NOW.isoformat(),
    })
    payload["artifacts"][0].update({
        "artifactType": artifact_type,
        "artifactState": artifact_state,
    })
    return payload


def _reconcile(payload: dict) -> ResearchTaskV12:
    return reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(payload), occurred_at=NOW
    )


def _authority_result_payload(task: ResearchTaskV12) -> tuple[dict, dict]:
    payload = task.model_dump(mode="json", by_alias=True)
    result_id = payload["finalResultAuthority"]["finalResultId"]
    result = next(
        row for row in payload["finalResults"]
        if row["finalResultId"] == result_id
    )
    return payload, result


def _degraded_payload() -> dict:
    payload = _payload()
    failed_assignment = deepcopy(payload["executionDecisions"][0]["assignments"][0])
    failed_assignment.update(
        {
            "assignmentId": "assignment_failed_attempt",
            "role": "reviewer",
            "completionCoverage": "progress_capable",
            "responsibleActorRef": {
                "actorType": "agent",
                "actorId": "reviewer_failed_attempt",
                "definitionVersion": 1,
            },
            "executorRef": {
                "executorType": "agent",
                "executorId": "reviewer_failed_attempt",
                "definitionVersion": 1,
            },
            "dependencyAssignmentIds": [
                payload["executionDecisions"][0]["assignments"][0]["assignmentId"]
            ],
        }
    )
    payload["executionDecisions"][0]["assignments"].append(failed_assignment)
    payload["executionDecisions"][0]["completionPolicy"][
        "reviewerAssignmentIds"
    ].append(failed_assignment["assignmentId"])
    failed = deepcopy(payload["workUnits"][0])
    failed.update(
        {
            "workUnitId": "wu_failed_attempt",
            "assignmentId": failed_assignment["assignmentId"],
            "responsibleActorRef": failed_assignment["responsibleActorRef"],
            "executorRef": failed_assignment["executorRef"],
            "idempotencyKey": "workunit:decision_contract_full:assignment_contract_full:failed",
            "status": "failed",
            "terminalReasonCode": "test_execution_failed",
            "finishedAt": NOW.isoformat(),
        }
    )
    payload["workUnits"].append(failed)
    payload["substageRuntimeStates"][0]["workUnitIds"].append(
        failed["workUnitId"]
    )
    return payload


def test_unresolved_required_scope_remains_open_without_final_result() -> None:
    task = _reconcile(
        _payload(runtime_status="skipped", artifact_type="unrelated_output")
    )

    completion = task.scientific_states[-1].execution_state["substageCompletion"][0]
    assert task.status.value == "running"
    assert task.completion_decisions == []
    assert task.final_results == []
    assert task.final_result_authority is None
    assert completion["completionCriteriaSatisfied"] is False
    assert completion["unresolvedCriterionIds"] == ["criterion_contract_full"]
    assert "runtime:sub_contract_full:skipped" in completion["gaps"]


def test_v3_final_result_rejects_legacy_evidence_projection() -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    result["evidenceRefs"] = ["artifact:mislabelled-as-evidence"]

    with pytest.raises(ValidationError, match="artifact_mislabeled_as_evidence"):
        ResearchTaskV12.model_validate(payload)


@pytest.mark.parametrize("status", (None, "failed", "unknown"))
def test_completed_requires_passed_validator_outcome(status: str | None) -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    if status is None:
        result["validationOutcome"] = None
    else:
        result["validationOutcome"]["status"] = status

    with pytest.raises(ValidationError, match="validator_outcome_not_passed"):
        ResearchTaskV12.model_validate(payload)


def test_authority_referencing_unknown_final_result_is_rejected() -> None:
    payload, _ = _authority_result_payload(_reconcile(_payload()))
    payload["finalResultAuthority"]["finalResultId"] = "final:does-not-exist"

    with pytest.raises(ValidationError, match="unknown FinalResult"):
        ResearchTaskV12.model_validate(payload)


def test_authority_referencing_historical_final_result_is_rejected() -> None:
    first = finalize_from_validated_facts(
        ResearchTaskV12.model_validate(_payload()),
        ResearchTaskV12.model_validate(_payload()).research_design_versions[0],
        occurred_at=NOW,
    )
    changed = first.model_dump(mode="json", by_alias=True)
    historical_id = changed["finalResultAuthority"]["finalResultId"]
    extra = deepcopy(changed["artifacts"][0])
    extra["artifactId"] = "artifact-new-current-source"
    extra["sourceCapabilityCallId"] = None
    extra["lineageKind"] = "direct_work_unit_output"
    changed["artifacts"].append(extra)
    changed_task = ResearchTaskV12.model_validate(changed)
    current = finalize_from_validated_facts(
        changed_task,
        changed_task.research_design_versions[0],
        occurred_at=datetime(2026, 8, 20, 1, 1, tzinfo=timezone.utc),
    )
    payload = current.model_dump(mode="json", by_alias=True)
    payload["finalResultAuthority"]["finalResultId"] = historical_id

    with pytest.raises(ValidationError, match="final_result_not_current"):
        ResearchTaskV12.model_validate(payload)


def test_only_accepted_completion_closes_to_final_result() -> None:
    full = _reconcile(_payload())
    partial = _reconcile(
        _payload(runtime_status="skipped", artifact_type="unrelated_output")
    )
    evidence_gap = _reconcile(
        _payload(runtime_status="skipped", artifact_state="needs_review")
    )
    degraded = _reconcile(_degraded_payload())

    assert all(
        row.status.value == "completed"
        and row.final_result_authority is not None
        and row.final_result_authority.integrity_contract_version
        == "scientific_completion_integrity_v3"
        for row in (full, degraded)
    )
    assert all(
        row.status.value == "running"
        and row.final_result_authority is None
        and row.final_results == []
        for row in (partial, evidence_gap)
    )


def test_failed_capability_call_cannot_remain_success_evidence() -> None:
    payload, _ = _authority_result_payload(_reconcile(_payload()))
    payload["capabilityCalls"][0]["status"] = "failed"

    with pytest.raises(ValidationError, match="completion_artifact_source_call_invalid"):
        ResearchTaskV12.model_validate(payload)


def test_empty_scientific_content_remains_an_explicit_evidence_gap() -> None:
    task = _reconcile(
        _payload(runtime_status="skipped", artifact_state="needs_review")
    )

    state = task.scientific_states[-1]
    assert task.status.value == "running"
    assert task.final_results == []
    assert state.execution_state["validatedArtifactRefs"] == []
    assert state.execution_state["substageCompletion"][0]["gaps"]


def test_v3_final_result_cannot_rewrite_scientific_state() -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    result["scientificResult"] = {"fabricated": "new scientific claim"}

    with pytest.raises(ValidationError, match="scientific_state_projection_failed"):
        ResearchTaskV12.model_validate(payload)


def test_v3_final_result_cannot_rewrite_scientific_source_refs() -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    result["scientificSourceRefs"] = ["artifact:not-a-state-source"]

    with pytest.raises(ValidationError, match="scientific_source_projection_failed"):
        ResearchTaskV12.model_validate(payload)


def test_v3_finalization_rejects_core_generated_scientific_answer() -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    result["directAnswer"] = "Candidate A is twice as good and should be prioritized."

    with pytest.raises(ValidationError, match="legacy_semantic_projection_present"):
        ResearchTaskV12.model_validate(payload)


def test_v3_keeps_completion_artifacts_out_of_evidence_and_findings() -> None:
    closed = _reconcile(_payload())
    result = closed.final_results[-1]

    assert result.evidence_refs == []
    assert result.key_findings == []
    assert closed.final_result_authority is not None
    assert closed.final_result_authority.result_artifact_refs


def test_contradictory_criterion_coverage_summary_fails_closed() -> None:
    payload, _ = _authority_result_payload(_reconcile(_payload()))
    current_state = payload["scientificStates"][-1]
    criterion = current_state["executionState"]["substageCompletion"][0]["criteria"][0]
    criterion["status"] = "unresolved"

    with pytest.raises(ValidationError, match="completion_coverage_mismatch"):
        ResearchTaskV12.model_validate(payload)


def test_completed_task_rejects_explicit_legacy_authority() -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    payload["finalResultAuthority"]["integrityContractVersion"] = "legacy_v0"
    result["validationOutcome"] = None

    with pytest.raises(ValidationError, match="legacy_v0 Final Result authority is read-only"):
        ResearchTaskV12.model_validate(payload)


def test_new_task_rejects_explicit_legacy_authority() -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    payload.update({"status": "running", "finishedAt": None})
    payload["finalResultAuthority"]["integrityContractVersion"] = "legacy_v0"
    result["validationOutcome"] = None

    with pytest.raises(ValidationError, match="legacy_v0 Final Result authority is read-only"):
        ResearchTaskV12.model_validate(payload)


def test_missing_integrity_contract_version_is_rejected() -> None:
    payload, _ = _authority_result_payload(_reconcile(_payload()))
    payload["finalResultAuthority"].pop("integrityContractVersion")

    with pytest.raises(ValidationError, match="Field required"):
        ResearchTaskV12.model_validate(payload)


def test_read_only_legacy_authority_cannot_enter_lifecycle() -> None:
    payload, result = _authority_result_payload(_reconcile(_payload()))
    payload["finalResultAuthority"]["integrityContractVersion"] = "legacy_v0"
    result["validationOutcome"] = None
    read_only = ResearchTaskV12.model_validate(
        payload,
        context={READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY: True},
    )

    with pytest.raises(DecisionPolicyViolation, match="legacy_final_result_authority_read_only"):
        reconcile_task_lifecycle_from_runtime(read_only, occurred_at=NOW)


def test_public_projection_marks_v3_validated_and_legacy_shadow_status() -> None:
    closed = _reconcile(_payload())
    source = closed.model_dump(mode="json", by_alias=True)
    source.update(
        {
            "ownerId": "owner-integrity",
            "workspaceId": "workspace-integrity",
            "createdBy": "actor-integrity",
            "title": "Scientific completion integrity",
            "currentStage": "insight_generation",
            "resultSource": "live",
            "stages": [
                "problem_modeling",
                "solution_design",
                "execution_validation",
                "insight_generation",
            ],
        }
    )

    validated = project_public_task(source).model_dump(mode="json", by_alias=True)
    assert validated["finalResultAuthority"]["integrityStatus"] == "validated"
    assert validated["finalResult"]["validationOutcome"]["status"] == "passed"

    legacy_source = deepcopy(source)
    legacy_source["finalResultAuthority"].pop("integrityContractVersion")
    with pytest.raises(ValidationError, match="Field required"):
        project_public_task(legacy_source)

    legacy = project_public_task_read_only(legacy_source).model_dump(mode="json", by_alias=True)
    assert legacy["finalResultAuthority"]["integrityStatus"] == "legacy_unvalidated"
    assert "integrityContractVersion" not in legacy_source["finalResultAuthority"]


def test_read_only_projection_rejects_explicit_legacy_authority() -> None:
    source, _ = _authority_result_payload(_reconcile(_payload()))
    source["finalResultAuthority"]["integrityContractVersion"] = "legacy_v0"

    with pytest.raises(ValidationError, match="legacy_v0 Final Result authority is read-only"):
        project_public_task_read_only(source)

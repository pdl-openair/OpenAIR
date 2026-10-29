from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
import yaml

from app.execution.research_dispatch_provider import DispatchSubmissionCompleted
from app.models.research_orchestration import ResearchTaskV12
from app.modules.ot_v3_scientific_state_materialization import (
    materialize_ot_v3_general_agent_result,
)
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch, DispatchFence, initial_external_attempt_identity,
    ResearchOrchestrationRepository,
    dispatch_capability_call_id,
)
from app.modules.scientific_completion_integrity import expected_final_result_fields
from app.modules.scientific_state_fact_projection import (
    project_validated_structured_result,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = (
    ROOT
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)
NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)
GENERAL_CONTRACT = "general_research_reasoning_artifact_v1"
ROLE_FACTS = {
    "opentrajectory:current_research_conclusion": (
        "ot_v3_current_research_conclusion"
    ),
    "opentrajectory:key_unknowns_candidate_explanations": (
        "ot_v3_key_unknowns_candidate_explanations"
    ),
    "opentrajectory:next_research_recommendation": (
        "ot_v3_next_research_recommendation"
    ),
}


def _repository() -> ResearchOrchestrationRepository:
    repository = ResearchOrchestrationRepository.__new__(
        ResearchOrchestrationRepository
    )
    repository._reconcile_task_lifecycle_with_frozen_snapshot = (
        lambda task, occurred_at=None: task
    )
    return repository


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]


def _authorized_dispatch(**values) -> AuthorizedDispatch:
    fence = DispatchFence(**{key: values[key] for key in DispatchFence.__dataclass_fields__})
    return AuthorizedDispatch(**values, external_attempt=initial_external_attempt_identity(fence))


def _running_task_and_fence() -> tuple[ResearchTaskV12, AuthorizedDispatch]:
    payload = _fixture()
    decision = payload["executionDecisions"][0]
    decision.update(
        {
            "purpose": "progress",
            "completionPolicy": None,
            "progressPolicy": {
                "requiredAssignmentIds": ["assignment_contract_full"],
                "requiredArtifactTypes": [],
                "acceptedResultCodes": "progressed",
            },
            "status": "dispatched",
            "outcome": None,
            "closedAt": None,
        }
    )
    decision["candidateEvaluations"][0]["preference"]["completionCoverage"] = (
        "progress_capable"
    )
    decision["assignments"][0]["completionCoverage"] = "progress_capable"
    decision["assignments"][0]["expectedArtifactTypes"] = []
    payload["workUnits"][0].update(
        {"expectedArtifactTypes": [], "status": "running", "finishedAt": None}
    )
    payload["artifacts"] = []
    payload["agentInteractionRecords"] = []
    payload["substageRuntimeStates"][0].update(
        {
            "status": "running",
            "activeExecutionDecisionId": "decision_contract_full",
            "artifactRefs": [],
        }
    )
    fence = _authorized_dispatch(
        dispatch_id="dispatch-ot-v3",
        task_id="task_contract_full",
        execution_decision_id="decision_contract_full",
        work_unit_id="wu_contract_full",
        idempotency_key="workunit:ot-v3",
        lease_owner="worker-ot-v3",
        attempt_count=1,
        authorization_task_revision=4,
        work_unit_payload={"executorRef": {"executorType": "agent"}},
    )
    payload["capabilityCalls"][0].update(
        {
            "capabilityCallId": dispatch_capability_call_id(fence),
            "idempotencyKey": "call:ot-v3:1",
            "status": "running",
            "outputArtifactRefs": [],
            "finishedAt": None,
        }
    )
    return ResearchTaskV12.model_validate(payload), fence


def _general_artifact(label: str) -> dict:
    return {
        "scope": {"responsibility": label},
        "reasoning_summary": {"accepted": f"validated {label}"},
        "assumptions": [],
        "uncertainty": [{"status": "explicit"}],
        "next_action": {
            "kind": "research_recommendation",
            "text": f"collect next evidence for {label}",
        },
    }


def _ot_v3_running_task(substage_definition_id: str) -> tuple[ResearchTaskV12, object]:
    from app.modules import research_execution_decision_service as decision_service
    from tests.test_research_orchestration_resolver import (
        _ot_v3_general_fallback_selection,
    )

    task, _resolution, _snapshot, prepared = _ot_v3_general_fallback_selection(
        substage_definition_id
    )
    preparation = decision_service.prepare_selection_execution(
        prepared,
        task,
        "agent:general_research_agent:1",
    )
    materialized = decision_service.freeze_selection_execution(
        prepared,
        task,
        preparation,
    )
    payload = materialized.task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    work = payload["workUnits"][0]
    dispatch = materialized.dispatches[0]
    work.update({"status": "running", "finishedAt": None})
    payload["substageRuntimeStates"][0].update(
        {
            "status": "running",
            "activeExecutionDecisionId": decision["executionDecisionId"],
        }
    )
    fence = _authorized_dispatch(
        dispatch_id=dispatch.dispatch_id,
        task_id=materialized.task.task_id,
        execution_decision_id=decision["executionDecisionId"],
        work_unit_id=work["workUnitId"],
        idempotency_key=dispatch.idempotency_key,
        lease_owner="worker-ot-v3",
        attempt_count=1,
        authorization_task_revision=materialized.task.revision,
        work_unit_payload=deepcopy(work),
    )
    call = deepcopy(_fixture()["capabilityCalls"][0])
    call.update(
        {
            "capabilityCallId": dispatch_capability_call_id(fence),
            "idempotencyKey": f"call:{work['workUnitId']}:1",
            "workUnitId": work["workUnitId"],
            "substageInstanceId": work["substageInstanceId"],
            "capabilityType": "agent_runtime",
            "capabilityId": "general_research_agent",
            "status": "running",
            "inputRefs": [],
            "outputArtifactRefs": [],
            "finishedAt": None,
        }
    )
    payload["capabilityCalls"] = [call]
    return ResearchTaskV12.model_validate(payload), fence


def _materializer_payload(
    substage_definition_id: str,
    *,
    result_id: str = "result:ot-v3",
    artifact_id: str = "artifact:ot-v3",
    check_status: str = "passed",
    workflow_version: int = 3,
) -> dict:
    return {
        "taskId": "task-ot-v3",
        "status": "running",
        "finishedAt": None,
        "activeResearchDesignRef": {
            "researchDesignId": "design-ot-v3",
            "version": 1,
        },
        "researchDesignVersions": [
            {
                "researchDesignId": "design-ot-v3",
                "version": 1,
                "workflowProvenanceRef": {
                    "workflowId": "opentrajectory_research_workflow",
                    "workflowVersion": workflow_version,
                },
            }
        ],
        "workUnits": [
            {
                "workUnitId": f"work:{substage_definition_id}",
                "researchDesignVersion": 1,
                "substageInstanceId": f"instance:{substage_definition_id}",
                "substageDefinitionId": substage_definition_id,
                "executorRef": {
                    "executorType": "agent",
                    "executorId": "general_research_agent",
                },
            }
        ],
        "results": [
            {
                "resultId": result_id,
                "workUnitId": f"work:{substage_definition_id}",
                "checkStatus": check_status,
                "artifactRefs": [artifact_id],
                "scientificStateVersions": [],
            }
        ],
        "artifacts": [
            {
                "artifactId": artifact_id,
                "workUnitId": f"work:{substage_definition_id}",
                "sourceResultId": result_id,
                "validatorResult": {
                    "status": "passed",
                    "artifactContract": GENERAL_CONTRACT,
                },
            }
        ],
        "scientificStates": [],
        "finalResultAuthority": None,
    }


def test_evidence_assessment_registry_projection_is_formal_v2_contract() -> None:
    registry = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text("utf-8"))
    row = next(
        item
        for item in registry["skills"]
        if item["skill_id"] == "ot_v3_evidence_assessment_synthesis"
    )
    declaration = row["scientific_state_projection"]
    assert declaration == {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [
            {
                "fact_key": "ot_v3_evidence_assessment",
                "source_field": "evidence_assessment_and_research_synthesis",
            }
        ],
    }
    assessment = {
        "research_record_refs": ["record:1"],
        "conflicts": [{"record_refs": ["record:1", "record:2"]}],
        "gaps": [{"missing": "direct measurement"}],
    }
    assert project_validated_structured_result(
        json.dumps({"evidence_assessment_and_research_synthesis": assessment}),
        declaration,
    ) == {"ot_v3_evidence_assessment": assessment}


def test_passed_evidence_assessment_uses_production_persistence_path() -> None:
    task, fence = _running_task_and_fence()
    payload = task.model_dump(mode="json", by_alias=True)
    decision = payload["executionDecisions"][0]
    completion = _fixture()["executionDecisions"][0]
    decision.update(
        {
            "purpose": "completion",
            "completionPolicy": completion["completionPolicy"],
            "progressPolicy": None,
            "candidateEvaluations": completion["candidateEvaluations"],
            "assignments": deepcopy(completion["assignments"]),
        }
    )
    expected_types = decision["assignments"][0]["expectedArtifactTypes"]
    payload["workUnits"][0].update(
        {
            "expectedArtifactTypes": expected_types,
            "status": "running",
            "finishedAt": None,
        }
    )
    payload["scientificStates"] = []
    task = ResearchTaskV12.model_validate(payload)
    registry = yaml.safe_load((ROOT / "skills" / "registry.yaml").read_text("utf-8"))
    declaration = next(
        row["scientific_state_projection"]
        for row in registry["skills"]
        if row["skill_id"] == "ot_v3_evidence_assessment_synthesis"
    )
    assessment = {
        "research_record_refs": ["record:1", "record:2"],
        "conflicts": [{"record_refs": ["record:1", "record:2"]}],
        "gaps": [{"missing": "direct measurement"}],
    }
    result_content = {
        "evidence_assessment_and_research_synthesis": assessment,
        "summary": {},
        "audit": {},
        "synthesis_warnings": [],
    }
    projected = project_validated_structured_result(
        json.dumps(result_content), declaration
    )

    updated, _catalog, _dispatches, _message = _repository()._task_with_completed_result(
        task,
        fence=fence,
        result=DispatchSubmissionCompleted(
            title="OT v3 evidence assessment",
            mime_type="application/json",
            content=json.dumps(result_content),
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="ot_v3_evidence_assessment_synthesis_v1",
        artifact_schema_digest="a" * 64,
        content_sha256="b" * 64,
        storage_type="local",
        storage_scope=task.task_id,
        storage_ref="durable/ot-v3-evidence/result.json",
        scientific_state_fact_projections=projected,
        scientific_state_projection_declaration=declaration,
        now=NOW,
    )

    state = updated.scientific_states[-1]
    assert state.scientific_facts["ot_v3_evidence_assessment"] == assessment
    assert state.scientific_facts["ot_v3_evidence_assessment"][
        "research_record_refs"
    ] == ["record:1", "record:2"]
    assert state.scientific_facts["ot_v3_evidence_assessment"]["conflicts"]
    assert state.scientific_facts["ot_v3_evidence_assessment"]["gaps"]
    assert state.source_refs == [updated.results[0].artifact_refs[0]]


@pytest.mark.parametrize("substage_definition_id,fact_key", ROLE_FACTS.items())
def test_passed_ot_v3_general_agent_result_uses_production_persistence_path(
    substage_definition_id: str,
    fact_key: str,
) -> None:
    task, fence = _ot_v3_running_task(substage_definition_id)
    artifact = _general_artifact(fact_key)

    updated, _catalog, _dispatches, _message = _repository()._task_with_completed_result(
        task,
        fence=fence,
        result=DispatchSubmissionCompleted(
            title=fact_key,
            mime_type="application/json",
            content=json.dumps(artifact),
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract=GENERAL_CONTRACT,
        artifact_schema_digest="a" * 64,
        content_sha256="b" * 64,
        storage_type="local",
        storage_scope=task.task_id,
        storage_ref=f"durable/{fact_key}/result.json",
        now=NOW,
    )

    result = updated.results[0]
    decision = updated.execution_decisions[0]
    runtime = updated.substage_runtime_states[0]
    state = next(
        row
        for row in updated.scientific_states
        if row.scientific_state_version in result.scientific_state_versions
    )
    value = state.scientific_facts[fact_key]
    assert decision.purpose.value == "completion"
    assert runtime.status.value == "completed"
    assert result.check_status.value == "passed"
    assert result.scientific_state_versions == [state.scientific_state_version]
    assert value["validatedArtifact"] == artifact
    assert value["provenance"] == {
        "sourceArtifactId": result.artifact_refs[0],
        "sourceResultId": result.result_id,
        "workUnitId": updated.work_units[0].work_unit_id,
        "substageInstanceId": updated.work_units[0].substage_instance_id,
        "substageDefinitionId": substage_definition_id,
        "researchDesignVersion": 1,
        "artifactContract": GENERAL_CONTRACT,
    }
    assert state.source_refs == [result.artifact_refs[0]]
    assert updated.scientific_states[-1].scientific_facts[fact_key] == value


def test_ot_v3_general_agent_wrong_artifact_contract_does_not_complete() -> None:
    fact_key = "ot_v3_current_research_conclusion"
    task, fence = _ot_v3_running_task(
        "opentrajectory:current_research_conclusion"
    )

    updated, _catalog, _dispatches, _message = _repository()._task_with_completed_result(
        task,
        fence=fence,
        result=DispatchSubmissionCompleted(
            title=fact_key,
            mime_type="application/json",
            content=json.dumps(_general_artifact(fact_key)),
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
        artifact_contract="unrelated_reasoning_artifact_v1",
        artifact_schema_digest="a" * 64,
        content_sha256="b" * 64,
        storage_type="local",
        storage_scope=task.task_id,
        storage_ref=f"durable/{fact_key}/invalid-result.json",
        now=NOW,
    )

    assert updated.execution_decisions[0].purpose.value == "completion"
    assert updated.substage_runtime_states[0].status.value != "completed"
    assert not any(
        row.interaction_type == "acceptance_recorded"
        for row in updated.agent_interaction_records
    )
    assert fact_key not in updated.scientific_states[-1].scientific_facts


def test_ot_v4_general_agent_result_uses_same_scientific_state_authority() -> None:
    fact_key = "ot_v3_current_research_conclusion"
    payload = _materializer_payload(
        "opentrajectory:current_research_conclusion",
        workflow_version=4,
    )

    updated = materialize_ot_v3_general_agent_result(
        task_payload=payload,
        result_id="result:ot-v3",
        validated_artifact_content=json.dumps(_general_artifact(fact_key)),
        occurred_at=NOW,
    )

    assert updated["scientificStates"][-1]["scientificFacts"][fact_key][
        "validatedArtifact"
    ] == _general_artifact(fact_key)


def test_four_ot_v3_facts_accumulate_and_final_result_copies_them_exactly() -> None:
    payload = _materializer_payload(
        "opentrajectory:current_research_conclusion"
    )
    evidence = {
        "research_record_refs": ["record:1"],
        "conflicts": [{"record_refs": ["record:1", "record:2"]}],
        "gaps": [{"missing": "boundary test"}],
    }
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {"ot_v3_evidence_assessment": evidence},
            "executionState": {},
            "sourceRefs": ["artifact:evidence"],
            "createdAt": "2026-09-30T00:00:00+00:00",
        }
    ]
    payload["workUnits"] = []
    payload["results"] = []
    payload["artifacts"] = []

    for index, (substage, fact_key) in enumerate(ROLE_FACTS.items(), start=1):
        current = _materializer_payload(
            substage,
            result_id=f"result:ot-v3:{index}",
            artifact_id=f"artifact:ot-v3:{index}",
        )
        payload["workUnits"].extend(current["workUnits"])
        payload["results"].extend(current["results"])
        payload["artifacts"].extend(current["artifacts"])
        payload = materialize_ot_v3_general_agent_result(
            task_payload=payload,
            result_id=f"result:ot-v3:{index}",
            validated_artifact_content=json.dumps(_general_artifact(fact_key)),
            occurred_at=datetime(2026, 9, 30, 0, index, tzinfo=timezone.utc),
        )

    latest = payload["scientificStates"][-1]
    assert set(ROLE_FACTS.values()) | {"ot_v3_evidence_assessment"} <= set(
        latest["scientificFacts"]
    )
    final_result = expected_final_result_fields(
        latest,
        decision={"uncertainty": [], "gaps": [], "limitations": []},
    )
    assert final_result["scientificResult"] == latest["scientificFacts"]
    assert final_result["scientificSourceRefs"] == latest["sourceRefs"]
    recommendation = final_result["scientificResult"][
        "ot_v3_next_research_recommendation"
    ]
    assert recommendation["validatedArtifact"]["next_action"]["kind"] == (
        "research_recommendation"
    )
    assert "executionDecision" not in recommendation
    assert "completionDecision" not in recommendation


def test_failed_judgment_preserves_evidence_and_failed_next_preserves_prior_facts() -> None:
    payload = _materializer_payload(
        "opentrajectory:current_research_conclusion",
        check_status="failed",
    )
    evidence = {"research_record_refs": ["record:accepted"]}
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {"ot_v3_evidence_assessment": evidence},
            "executionState": {},
            "sourceRefs": ["artifact:evidence"],
            "createdAt": "2026-09-30T00:00:00+00:00",
        }
    ]
    payload = materialize_ot_v3_general_agent_result(
        task_payload=payload,
        result_id="result:ot-v3",
        validated_artifact_content=json.dumps(_general_artifact("judgment")),
        occurred_at=NOW,
    )
    assert payload["scientificStates"][-1]["scientificFacts"] == {
        "ot_v3_evidence_assessment": evidence
    }
    payload["workUnits"] = []
    payload["results"] = []
    payload["artifacts"] = []

    for index, substage in enumerate(
        (
            "opentrajectory:current_research_conclusion",
            "opentrajectory:key_unknowns_candidate_explanations",
        ),
        start=1,
    ):
        accepted = _materializer_payload(
            substage,
            result_id=f"result:accepted:{index}",
            artifact_id=f"artifact:accepted:{index}",
        )
        payload["workUnits"].extend(accepted["workUnits"])
        payload["results"].extend(accepted["results"])
        payload["artifacts"].extend(accepted["artifacts"])
        payload = materialize_ot_v3_general_agent_result(
            task_payload=payload,
            result_id=f"result:accepted:{index}",
            validated_artifact_content=json.dumps(_general_artifact(substage)),
            occurred_at=NOW,
        )
    accepted_state = deepcopy(payload["scientificStates"][-1])
    failed = _materializer_payload(
        "opentrajectory:next_research_recommendation",
        result_id="result:failed",
        artifact_id="artifact:failed",
        check_status="failed",
    )
    payload["workUnits"].extend(failed["workUnits"])
    payload["results"].extend(failed["results"])
    payload["artifacts"].extend(failed["artifacts"])

    result = materialize_ot_v3_general_agent_result(
        task_payload=payload,
        result_id="result:failed",
        validated_artifact_content=json.dumps(_general_artifact("next")),
        occurred_at=NOW,
    )

    assert result["scientificStates"][-1] == accepted_state
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["ot_v3_evidence_assessment"] == evidence
    assert "ot_v3_current_research_conclusion" in facts
    assert "ot_v3_key_unknowns_candidate_explanations" in facts
    assert "ot_v3_next_research_recommendation" not in facts


@pytest.mark.parametrize(
    "mutation",
    ("legacy_substage", "other_workflow", "wrong_contract", "superseded_design"),
)
def test_ot_v3_projection_is_fail_closed_outside_exact_authority(mutation: str) -> None:
    payload = _materializer_payload(
        "opentrajectory:current_research_conclusion"
    )
    if mutation == "legacy_substage":
        payload["workUnits"][0]["substageDefinitionId"] = (
            "opentrajectory:findings_synthesis"
        )
    elif mutation == "other_workflow":
        payload["researchDesignVersions"][0]["workflowProvenanceRef"][
            "workflowId"
        ] = "other_workflow"
    elif mutation == "wrong_contract":
        payload["artifacts"][0]["validatorResult"]["artifactContract"] = (
            "other_artifact_v1"
        )
    else:
        payload["activeResearchDesignRef"]["version"] = 2

    result = materialize_ot_v3_general_agent_result(
        task_payload=payload,
        result_id="result:ot-v3",
        validated_artifact_content=json.dumps(_general_artifact("judgment")),
        occurred_at=NOW,
    )

    assert result["scientificStates"] == []


def test_reprocessing_same_ot_v3_result_is_idempotent() -> None:
    payload = _materializer_payload(
        "opentrajectory:key_unknowns_candidate_explanations"
    )
    content = json.dumps(_general_artifact("unknowns"))
    first = materialize_ot_v3_general_agent_result(
        task_payload=payload,
        result_id="result:ot-v3",
        validated_artifact_content=content,
        occurred_at=NOW,
    )
    second = materialize_ot_v3_general_agent_result(
        task_payload=first,
        result_id="result:ot-v3",
        validated_artifact_content=content,
        occurred_at=datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc),
    )

    assert second == first
    assert len(second["scientificStates"]) == 1

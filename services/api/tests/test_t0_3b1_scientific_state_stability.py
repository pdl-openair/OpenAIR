from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.models.research_orchestration import ResearchDesignVersion, ResearchTaskV12
from app.modules.pvd_scientific_state_materialization import (
    materialize_experimental_system_context,
)
from app.modules.research_design_activation import scientific_states_for_confirmed_design
from app.modules.research_finalization import (
    finalize_from_validated_facts,
    record_scientific_state_from_durable_facts,
)
from app.modules.scientific_completion_integrity import evaluate_final_result_integrity


NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _base_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]


def _finalizable_payload() -> dict:
    payload = _base_payload()
    payload["status"] = "running"
    payload["substageRuntimeStates"][0].update(
        {
            "status": "completed",
            "statusReasonCode": None,
            "activeExecutionDecisionId": None,
            "updatedAt": NOW.isoformat(),
        }
    )
    payload["workUnits"][0].update(
        {
            "status": "completed",
            "terminalReasonCode": None,
            "finishedAt": NOW.isoformat(),
        }
    )
    payload["artifacts"][0]["artifactState"] = "usable"
    return payload


def _accepted_pvd_facts() -> dict:
    return {
        "experimental_system_contexts": [
            {
                "experimental_system_context_ref": "pvd-context-1",
                "experimental_system_context": {
                    "material_system": "accepted-context-v1"
                },
            }
        ],
        "current_experimental_system_context_ref": "pvd-context-1",
        "experiment_history": [{"experiment_ref": "accepted-experiment-1"}],
    }


def _with_accepted_pvd_state(payload: dict, *, execution_state: dict | None = None) -> dict:
    payload = deepcopy(payload)
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": _accepted_pvd_facts(),
            "executionState": deepcopy(execution_state or {}),
            "sourceRefs": ["artifact-domain-fact-source"],
            "createdAt": "2026-09-12T07:00:00+00:00",
        }
    ]
    return payload


def _finalized_with_pvd_facts() -> ResearchTaskV12:
    task = ResearchTaskV12.model_validate(_with_accepted_pvd_state(_finalizable_payload()))
    return finalize_from_validated_facts(
        task,
        task.research_design_versions[0],
        occurred_at=NOW,
    )


def test_finalization_preserves_accepted_pvd_facts_and_keeps_completion_evidence_separate() -> None:
    finalized = _finalized_with_pvd_facts()
    state = finalized.scientific_states[-1]
    artifact_id = finalized.artifacts[0].artifact_id

    assert state.scientific_facts == _accepted_pvd_facts()
    assert state.source_refs == ["artifact-domain-fact-source"]
    assert state.execution_state["validatedArtifactRefs"] == [artifact_id]
    assert "validatedArtifactRefs" not in state.scientific_facts

    decision = finalized.completion_decisions[-1]
    result = finalized.final_results[-1]
    authority = finalized.final_result_authority
    assert authority is not None
    assert decision.source_refs == [artifact_id]
    assert result.evidence_refs == []
    assert result.scientific_source_refs == ["artifact-domain-fact-source"]
    assert authority.result_artifact_refs == [artifact_id]
    integrity = evaluate_final_result_integrity(
        finalized.model_dump(mode="json", by_alias=True),
        require_persisted_outcome=True,
    )
    assert integrity["passed"] is True
    assert integrity["errorCodes"] == []


def test_project_acceptance_fact_satisfies_domain_professional_completion_criterion() -> None:
    payload = _finalizable_payload()
    criterion = payload["researchDesignVersions"][0]["instances"][0]["acceptanceCriteria"][0]
    criterion.update(
        {
            "criterionKind": "professional_judgment",
            "judgmentRequirement": "domain_professional",
            "evaluatorType": "agent",
            "evaluatorRef": "result_reliability_audit",
        }
    )
    assignment_criterion = payload["executionDecisions"][0]["assignments"][0][
        "acceptanceCriteria"
    ][0]
    assignment_criterion.update(
        {
            "criterionKind": "professional_judgment",
            "judgmentRequirement": "domain_professional",
            "evaluatorType": "agent",
            "evaluatorRef": "result_reliability_audit",
        }
    )
    base_interaction = payload["agentInteractionRecords"][0]
    payload["agentInteractionRecords"].append(
        {
            **base_interaction,
            "interactionId": "interaction_contract_full:acceptance",
            "idempotencyKey": "interaction:acceptance:wu_contract_full",
            "interactionType": "acceptance_recorded",
            "verdict": {
                "verdictType": "acceptance",
                "decision": "approved",
                "rationaleCode": "review_usable",
            },
            "summary": "Approved review accepted by the current project.",
        }
    )

    task = ResearchTaskV12.model_validate(payload)
    finalized = finalize_from_validated_facts(
        task,
        task.research_design_versions[0],
        occurred_at=NOW,
    )

    completion_row = finalized.scientific_states[-1].execution_state[
        "substageCompletion"
    ][0]
    assert completion_row["completionCriteriaSatisfied"] is True
    assert completion_row["criteria"][0]["status"] == "satisfied"
    assert finalized.completion_decisions[-1].can_complete is True


def test_ordinary_task_without_domain_facts_still_finalizes() -> None:
    task = ResearchTaskV12.model_validate(_finalizable_payload())
    finalized = finalize_from_validated_facts(
        task,
        task.research_design_versions[0],
        occurred_at=NOW,
    )
    state = finalized.scientific_states[-1]

    assert state.scientific_facts == {}
    assert state.source_refs == []
    assert state.execution_state["validatedArtifactRefs"] == [
        finalized.artifacts[0].artifact_id
    ]
    assert finalized.final_result_authority is not None


@pytest.mark.parametrize(
    ("runtime_status", "work_status", "call_status", "artifact_state", "decision_status"),
    [
        ("skipped", "completed", "succeeded", "usable", "dispatched"),
        ("blocked_recoverable", "blocked", "failed", "needs_review", "closed"),
        ("failed", "failed", "failed", "needs_review", "closed"),
    ],
    ids=("partial", "degraded", "failed"),
)
def test_non_full_execution_snapshot_never_clears_accepted_scientific_facts(
    runtime_status: str,
    work_status: str,
    call_status: str,
    artifact_state: str,
    decision_status: str,
) -> None:
    payload = _with_accepted_pvd_state(_base_payload())
    runtime = payload["substageRuntimeStates"][0]
    runtime.update(
        {
            "status": runtime_status,
            "activeExecutionDecisionId": (
                payload["executionDecisions"][0]["executionDecisionId"]
                if decision_status == "dispatched"
                else None
            ),
            "statusReasonCode": None if runtime_status == "skipped" else f"test_{runtime_status}",
            "updatedAt": NOW.isoformat(),
        }
    )
    work = payload["workUnits"][0]
    work.update(
        {
            "status": work_status,
            "terminalReasonCode": None if work_status == "completed" else f"test_{work_status}",
            "finishedAt": NOW.isoformat(),
        }
    )
    payload["artifacts"][0]["artifactState"] = artifact_state
    call = payload["capabilityCalls"][0]
    call.update(
        {
            "status": call_status,
            "finishedAt": NOW.isoformat(),
            "outputArtifactRefs": (
                [payload["artifacts"][0]["artifactId"]]
                if call_status == "succeeded"
                else []
            ),
        }
    )
    if decision_status == "closed":
        payload["executionDecisions"][0].update(
            {
                "status": "closed",
                "outcome": "failed",
                "closedAt": NOW.isoformat(),
            }
        )

    task = ResearchTaskV12.model_validate(payload)
    snapshotted, state = record_scientific_state_from_durable_facts(
        task,
        task.research_design_versions[0],
        occurred_at=NOW,
    )

    assert state["scientificFacts"] == _accepted_pvd_facts()
    assert state["sourceRefs"] == ["artifact-domain-fact-source"]
    assert snapshotted.scientific_states[-1].scientific_facts == _accepted_pvd_facts()


def test_new_confirmed_design_inherits_accepted_facts_and_resets_only_execution_state() -> None:
    task = ResearchTaskV12.model_validate(
        _with_accepted_pvd_state(
            _base_payload(),
            execution_state={"validatedArtifactRefs": ["artifact-old-completion"]},
        )
    )
    design_payload = task.research_design_versions[0].model_dump(
        mode="json", by_alias=True
    )
    design_payload["version"] = 2
    design_payload["supersedesVersion"] = 1
    design_payload["coverageReport"]["designRef"]["version"] = 2
    design_payload["confirmationRef"] = "confirmation-v2"
    design_v2 = ResearchDesignVersion.model_validate(design_payload)

    states = scientific_states_for_confirmed_design(
        task=task,
        design=design_v2,
        activated_at=NOW,
    )
    current = states[-1]

    assert current.research_design_version == 2
    assert current.scientific_facts == _accepted_pvd_facts()
    assert current.source_refs == ["artifact-domain-fact-source"]
    assert current.execution_state == {}


def test_repeated_finalization_is_idempotent_for_scientific_state_and_final_authority() -> None:
    first = _finalized_with_pvd_facts()
    second = finalize_from_validated_facts(
        first,
        first.research_design_versions[0],
        occurred_at=NOW,
    )

    assert len(second.scientific_states) == len(first.scientific_states)
    assert len(second.completion_decisions) == len(first.completion_decisions)
    assert len(second.final_results) == len(first.final_results)
    assert second.scientific_states == first.scientific_states
    assert second.final_result_authority == first.final_result_authority


def test_pvd_materializer_can_extend_domain_facts_after_finalization() -> None:
    finalized = _finalized_with_pvd_facts()
    before = finalized.scientific_states[-1]

    materialized = materialize_experimental_system_context(
        task_payload=finalized.model_dump(mode="json", by_alias=True),
        projected_facts={
            "experimental_system_context": {"material_system": "accepted-context-v2"}
        },
        source_artifact_id="artifact-pvd-context-v2",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 12, 8, 1, tzinfo=timezone.utc),
    )
    current = materialized["scientificStates"][-1]

    assert len(current["scientificFacts"]["experimental_system_contexts"]) == 2
    assert current["scientificFacts"]["experiment_history"] == [
        {"experiment_ref": "accepted-experiment-1"}
    ]
    assert current["executionState"] == before.execution_state
    assert current["sourceRefs"] == [
        "artifact-domain-fact-source",
        "artifact-pvd-context-v2",
    ]
    assert materialized["finalResultAuthority"] is None

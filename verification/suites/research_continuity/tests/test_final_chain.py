"""The real existing finalization/lifecycle chain in an isolated process."""

from copy import deepcopy

import pytest
from app.models.research_orchestration import CompletionDecision, ResearchTaskV12
from app.modules.research_completion_decision import record_project_completion_decision
from app.modules.research_execution_decision_service import reconcile_task_lifecycle_from_runtime
from pydantic import ValidationError
from test_round_end import NOW, settled_scope


def test_unmet_goal_ends_only_via_real_completion_and_final_result_chain():
    task, design, _ = settled_scope()
    model = ResearchTaskV12.model_validate(task)
    finished = reconcile_task_lifecycle_from_runtime(model, occurred_at=NOW)
    assert finished.status.value == "completed"
    decision = finished.completion_decisions[-1]
    assert decision.completion_authority_version == "project_completion_v2"
    assert decision.can_complete is False
    assert decision.requirement_assessments[0].status == "unresolved"
    assert decision.round_end is not None
    assert (
        finished.final_result_authority.final_result_id
        == finished.final_results[-1].final_result_id
    )
    assert finished.final_results[-1].disposition.value == "partial"
    assert finished.artifacts == model.artifacts
    assert finished.work_units == model.work_units
    assert finished.final_results[-1].scientific_result == {}
    assert reconcile_task_lifecycle_from_runtime(finished, occurred_at=NOW) == finished


def test_round_decision_is_idempotent_and_old_v1_goal_success_stays_compatible():
    task, _, _ = settled_scope()
    model = ResearchTaskV12.model_validate(task)
    design = model.research_design_versions[0]
    state = model.scientific_states[-1]
    once, decision = record_project_completion_decision(model, design, state, occurred_at=NOW)
    twice, replayed = record_project_completion_decision(once, design, state, occurred_at=NOW)
    assert once == twice and decision == replayed
    assert decision.can_complete is False
    original = deepcopy(task)
    criterion = original["researchDesignVersions"][0]["instances"][0]["acceptanceCriteria"][0]
    criterion.update(
        judgmentRequirement="deterministic", evaluatorType="deterministic", evaluatorRef=None
    )
    finished = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(original), occurred_at=NOW
    )
    assert finished.status.value == "completed"
    legacy = finished.completion_decisions[-1]
    assert legacy.completion_authority_version == "project_completion_v1"
    assert legacy.can_complete is True and legacy.round_end is None
    assert "roundEnd" not in legacy.model_dump(mode="json", by_alias=True)


def test_final_result_rejects_tampered_round_sources_and_active_execution():
    task, _, _ = settled_scope()
    finished = reconcile_task_lifecycle_from_runtime(
        ResearchTaskV12.model_validate(task), occurred_at=NOW
    )
    for corruption in ("source", "running", "unknown", "scope", "achievement"):
        payload = finished.model_dump(mode="json", by_alias=True)
        if corruption == "source":
            payload["completionDecisions"][-1]["roundEnd"]["sourceDecisionRefs"] = ["invented"]
        elif corruption in {"running", "unknown"}:
            payload["capabilityCalls"][0]["status"] = (
                "running" if corruption == "running" else "external_result_unknown"
            )
        elif corruption == "scope":
            payload["completionDecisions"][-1]["roundEnd"]["instanceRefs"][0][
                "instanceRevision"
            ] = 2
        else:
            payload["completionDecisions"][-1]["canComplete"] = True
        with pytest.raises((ValidationError, ValueError)):
            ResearchTaskV12.model_validate(payload)


def test_v2_cannot_be_used_to_relabel_success_or_omit_closure_receipt():
    task, _, _ = settled_scope()
    model = ResearchTaskV12.model_validate(task)
    _, decision = record_project_completion_decision(
        model, model.research_design_versions[0], model.scientific_states[-1], occurred_at=NOW
    )
    payload = decision.model_dump(mode="json", by_alias=True)
    payload.pop("roundEnd")
    with pytest.raises(ValidationError, match="v2 requires"):
        CompletionDecision.model_validate(payload)


def test_intermediate_round_decision_rejects_forged_lineage_before_final_result():
    task, _, _ = settled_scope()
    model = ResearchTaskV12.model_validate(task)
    decided, _ = record_project_completion_decision(
        model, model.research_design_versions[0], model.scientific_states[-1], occurred_at=NOW
    )
    assert decided.final_result_authority is None
    payload = decided.model_dump(mode="json", by_alias=True)
    payload["completionDecisions"][-1]["roundEnd"]["sourceArtifactRefs"] = ["invented"]
    with pytest.raises(ValidationError, match="round end source artifact identity"):
        ResearchTaskV12.model_validate(payload)

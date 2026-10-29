"""Canonical software snapshots, never real task execution or user acceptance."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_completion_decision import expected_project_completion_evaluation
from app.modules.research_finalization import record_scientific_state_from_durable_facts
from app.modules.research_round_end import derive_round_end

NOW = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)


def settled_scope():
    root = Path(__file__).resolve().parents[4]
    task = json.loads(
        (root / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json").read_text(
            encoding="utf-8"
        )
    )["researchTask"]
    task["workUnits"][0].update(status="completed", finishedAt=NOW.isoformat())
    task["executionDecisions"][0].update(
        status="closed", outcome="succeeded", closedAt=NOW.isoformat()
    )
    task["substageRuntimeStates"][0].update(status="completed", activeExecutionDecisionId=None)
    task["artifacts"][0]["artifactState"] = "usable"
    criterion = task["researchDesignVersions"][0]["instances"][0]["acceptanceCriteria"][0]
    criterion.update(
        judgmentRequirement="domain_professional",
        evaluatorType="agent",
        evaluatorRef="review-agent",
    )
    model = ResearchTaskV12.model_validate(task)
    design = model.research_design_versions[0]
    snapshotted, state = record_scientific_state_from_durable_facts(model, design, occurred_at=NOW)
    return (
        snapshotted.model_dump(mode="json", by_alias=True),
        design.model_dump(mode="json", by_alias=True),
        state,
    )


def test_actual_scope_closure_does_not_relabel_unresolved_scientific_criteria():
    task, design, state = settled_scope()
    before = deepcopy((task, design, state))
    evaluation = expected_project_completion_evaluation(design, state)
    assert evaluation["canComplete"] is False
    proof = derive_round_end(task, design, state)
    assert proof["reason"] == "confirmed_scope_settled"
    assert proof["sourceDecisionRefs"] == [task["executionDecisions"][0]["executionDecisionId"]]
    assert proof["sourceArtifactRefs"] == [task["artifacts"][0]["artifactId"]]
    assert (task, design, state) == before


@pytest.mark.parametrize("status", ["queued", "running", "reviewing", "external_result_unknown"])
def test_active_or_unknown_work_prevents_round_closure(status):
    task, design, state = settled_scope()
    task["workUnits"][0]["status"] = status
    assert derive_round_end(task, design, state) is None


@pytest.mark.parametrize("status", ["queued", "running", "external_result_unknown"])
def test_canonical_action_status_is_not_ignored_by_completed_runtime(status):
    task, design, state = settled_scope()
    task["capabilityCalls"][0]["status"] = status
    assert derive_round_end(task, design, state) is None


@pytest.mark.parametrize("status", ["planned", "ready", "running", "blocked_recoverable", "failed"])
def test_remaining_responsibility_is_not_called_scope_exhaustion(status):
    task, design, state = settled_scope()
    task["substageRuntimeStates"][0]["status"] = status
    assert derive_round_end(task, design, state) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "dispatched"),
        ("closedAt", None),
        ("substageInstanceRevision", 2),
        ("researchDesignVersion", 2),
    ],
)
def test_closed_decision_must_cover_exact_current_instance(field, value):
    task, design, state = settled_scope()
    task["executionDecisions"][0][field] = value
    assert derive_round_end(task, design, state) is None


def test_stale_or_unconfirmed_scope_and_user_stop_do_not_end_the_round():
    for change in ("state", "scope", "draft", "stop"):
        task, design, state = settled_scope()
        if change == "state":
            newer = {
                **deepcopy(state),
                "scientificStateVersion": state["scientificStateVersion"] + 1,
            }
            task["scientificStates"].append(newer)
        elif change == "scope":
            task["activeResearchDesignRef"]["version"] += 1
        elif change == "draft":
            design["status"] = "draft"
        else:
            task["status"] = "cancelled"
        assert derive_round_end(task, design, state) is None


@pytest.mark.parametrize(
    "corruption", ["artifact", "call", "work", "empty", "invented", "coverage"]
)
def test_terminal_node_count_cannot_replace_live_material_and_scope_proof(corruption):
    task, design, state = settled_scope()
    if corruption == "artifact":
        task["artifacts"][0]["artifactState"] = "failed"
    elif corruption == "call":
        task["capabilityCalls"][0]["outputArtifactRefs"] = []
    elif corruption == "work":
        task["workUnits"][0]["researchDesignVersion"] = 2
    elif corruption == "empty":
        state["executionState"]["validatedArtifactRefs"] = []
    elif corruption == "invented":
        state["executionState"]["validatedArtifactRefs"] = ["invented-artifact"]
    else:
        state["executionState"]["substageCompletion"] = []
    task["scientificStates"][-1] = state
    assert derive_round_end(task, design, state) is None


def test_unknown_scientific_facts_are_preserved_and_do_not_become_a_quality_gate():
    task, design, state = settled_scope()
    state["scientificFacts"] = {"measurement": None, "limitations": ["not measured"]}
    task["scientificStates"][-1] = deepcopy(state)
    assert derive_round_end(task, design, state) is not None
    assert state["scientificFacts"]["measurement"] is None


def test_skipped_scope_requires_legitimate_reason_and_same_closed_owner():
    task, design, state = settled_scope()
    runtime = task["substageRuntimeStates"][0]
    runtime.update(status="skipped", statusReasonCode="semantic_material_insufficient")
    state["executionState"]["substageCompletion"][0]["runtimeStatus"] = "skipped"
    task["scientificStates"][-1] = deepcopy(state)
    assert derive_round_end(task, design, state) is not None
    runtime["statusReasonCode"] = "adapter_implementation_missing"
    assert derive_round_end(task, design, state) is None

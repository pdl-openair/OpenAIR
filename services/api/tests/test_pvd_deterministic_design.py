"""Unit tests for the deterministic PVD full-chain ResearchDesign generator.

These tests verify that selecting the PVD workflow produces a design covering
all 8 substages (S1..S8) with zero LLM planner calls, the S5→S6 dependency
edge removed (so chain 1 does not stall on dormant S5), and S5 ordered last
(so the generic scheduler runs the full chain before S5 becomes the sole
READY candidate and honestly lands in needs_input).
"""
from __future__ import annotations

from typing import Any

import pytest
from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.pvd_deterministic_design import (
    PVD_WORKFLOW_ID,
    build_pvd_deterministic_brief,
    build_pvd_deterministic_provider_result,
    compile_pvd_full_chain_proposal,
    prepare_deterministic_pvd_planner_attempt,
)
from app.modules.pvd_workflow_admission import pvd_workflow_planning_admission
from app.modules.research_design_service import validate_research_design_proposal
from app.modules.workflow_planning import workflow_planning_bundle

_RAW_INPUT = (
    "研究 AlScN 薄膜磁控溅射工艺，目标优化功率、N2 流量、Ar 流量和沉积时间四个参数，"
    "获得残余应力低、膜厚均匀的工艺窗口。设备：靶基距 60mm，功率 100-300W。"
)

# Definition ids for the 8 PVD substages.
_S1 = "pvd:research_goal_equipment_constraints"
_S2 = "pvd:research_retrieval_experiment_design"
_S3 = "pvd:literature_experiment_record_verification"
_S4 = "pvd:evidence_applicability_comparability_analysis"
_S5 = "pvd:experimental_feedback_round_analysis"
_S6 = "pvd:process_factor_candidate_experiment_analysis"
_S7 = "pvd:parameter_experiment_plan_feasibility_validation"
_S8 = "pvd:round_experiment_plan_parameter_recommendation"

_ALL_DEFINITIONS = {_S1, _S2, _S3, _S4, _S5, _S6, _S7, _S8}


@pytest.fixture(scope="module")
def pvd_planning_bundle() -> Any:
    selection = pvd_workflow_planning_admission()
    source = ProductionDefinitionSource(planning_only=True)
    return workflow_planning_bundle(
        selection=selection, raw_goal=_RAW_INPUT, definition_source=source
    )


def _proposal(pvd_planning_bundle: Any) -> dict[str, Any]:
    return compile_pvd_full_chain_proposal(
        raw_input=_RAW_INPUT,
        definition_catalog=pvd_planning_bundle.definition_catalog,
        workflow_planning_context=pvd_planning_bundle.planning_context,
    )


def test_pvd_workflow_id_is_the_v3_identifier() -> None:
    assert PVD_WORKFLOW_ID == "pvd_process_optimization_workflow"


def test_deterministic_brief_selects_both_conditional_responsibilities(
    pvd_planning_bundle: Any,
) -> None:
    brief = build_pvd_deterministic_brief(
        raw_input=_RAW_INPUT,
        workflow_planning_context=pvd_planning_bundle.planning_context,
    )
    # Both conditional responsibilities (S4, S5) receive a non-empty context
    # delta so the assembly materializes them as planned_work.
    assert len(brief.responsibility_deltas) == 2
    choices = {d.responsibility_choice for d in brief.responsibility_deltas}
    assert len(choices) == 2
    for delta in brief.responsibility_deltas:
        assert delta.work_item_scientific_contexts
        assert delta.reuse_candidate_key is None


def test_proposal_covers_all_eight_substages(pvd_planning_bundle: Any) -> None:
    proposal = _proposal(pvd_planning_bundle)
    definition_ids = {row["definitionId"] for row in proposal["instances"]}
    assert definition_ids == _ALL_DEFINITIONS


def test_s6_does_not_depend_on_s5(pvd_planning_bundle: Any) -> None:
    proposal = _proposal(pvd_planning_bundle)
    s5_key = next(
        row["instanceKey"]
        for row in proposal["instances"]
        if row["definitionId"] == _S5
    )
    s6 = next(
        row for row in proposal["instances"] if row["definitionId"] == _S6
    )
    upstream_keys = {dep["upstreamInstanceKey"] for dep in s6["dependencies"]}
    assert s5_key not in upstream_keys, "S6 must not depend on dormant S5"
    # S6 must still depend on S4 (evidence feeds candidate analysis).
    s4_key = next(
        row["instanceKey"]
        for row in proposal["instances"]
        if row["definitionId"] == _S4
    )
    assert s4_key in upstream_keys


def test_s5_is_last_in_instance_order(pvd_planning_bundle: Any) -> None:
    proposal = _proposal(pvd_planning_bundle)
    instances = proposal["instances"]
    assert instances[-1]["definitionId"] == _S5


def test_proposal_passes_validation(pvd_planning_bundle: Any) -> None:
    proposal = _proposal(pvd_planning_bundle)
    validated = validate_research_design_proposal(proposal)
    assert len(validated.instances) == 8


def test_reentry_authority_flows_from_frozen_definitions(
    pvd_planning_bundle: Any,
) -> None:
    """The registry snapshot's executionReentry must survive compilation.

    Repeatable re-entry arming (S4/S5 close → S6 reactivation) reads
    ``SubstageInstance.repeatable_reentry_authority``; if the compiler drops
    the Definition-owned policy, re-entry silently never triggers.
    """
    source = ProductionDefinitionSource(planning_only=True)
    authority_by_definition = {
        row["definitionId"]: row["instancePolicy"]
        for row in pvd_planning_bundle.definition_catalog
    }
    s6 = source.resolve(_S6)
    assert s6 is not None
    assert s6.repeatable_reentry_authority is not None
    assert set(
        s6.repeatable_reentry_authority.trigger_definition_ids
    ) == {_S4, _S5}
    # Definitions without an executionReentry policy stay fail-closed (None).
    assert authority_by_definition[_S5].get("executionReentry") is None
    s5 = source.resolve(_S5)
    assert s5 is not None
    assert s5.repeatable_reentry_authority is None


def test_provider_result_has_stable_call_ref_and_no_sources(
    pvd_planning_bundle: Any,
) -> None:
    result = build_pvd_deterministic_provider_result(
        raw_input=_RAW_INPUT,
        definition_catalog=pvd_planning_bundle.definition_catalog,
        workflow_planning_context=pvd_planning_bundle.planning_context,
    )
    assert result.provider_call_ref.startswith("pvd-deterministic:")
    assert len(result.provider_call_ref) > len("pvd-deterministic:")
    assert result.planning_source_usages == ()
    assert isinstance(result.proposal, dict)
    assert len(result.proposal["instances"]) == 8


def test_provider_result_is_deterministic(pvd_planning_bundle: Any) -> None:
    """Same input → same provider_call_ref and same instance keys."""
    kwargs = dict(
        raw_input=_RAW_INPUT,
        definition_catalog=pvd_planning_bundle.definition_catalog,
        workflow_planning_context=pvd_planning_bundle.planning_context,
    )
    a = build_pvd_deterministic_provider_result(**kwargs)
    b = build_pvd_deterministic_provider_result(**kwargs)
    assert a.provider_call_ref == b.provider_call_ref
    keys_a = [row["instanceKey"] for row in a.proposal["instances"]]
    keys_b = [row["instanceKey"] for row in b.proposal["instances"]]
    assert keys_a == keys_b


def test_prepared_attempt_executes_without_llm(pvd_planning_bundle: Any) -> None:
    """The prepared attempt produces a PlannerProviderResult with no HTTP call."""
    prepared = prepare_deterministic_pvd_planner_attempt(
        raw_input=_RAW_INPUT,
        ordinal=1,
        definition_catalog=pvd_planning_bundle.definition_catalog,
        workflow_planning_context=pvd_planning_bundle.planning_context,
    )
    result = prepared.execute()
    assert result.provider_call_ref.startswith("pvd-deterministic:")
    assert len(result.proposal["instances"]) == 8


def test_no_llm_transport_is_invoked(
    pvd_planning_bundle: Any, monkeypatch
) -> None:
    """Generating the PVD design must not touch any LLM HTTP transport."""

    def _forbidden_post(*_args, **_kwargs):
        raise AssertionError("PVD deterministic design must not call the LLM provider")

    monkeypatch.setattr(
        "app.execution.research_start_provider_adapter.HttpxResearchStartTransport.post",
        _forbidden_post,
    )
    monkeypatch.setattr(
        "app.execution.llm_structured_runner.run_skill_with_llm",
        _forbidden_post,
    )
    result = build_pvd_deterministic_provider_result(
        raw_input=_RAW_INPUT,
        definition_catalog=pvd_planning_bundle.definition_catalog,
        workflow_planning_context=pvd_planning_bundle.planning_context,
    )
    assert len(result.proposal["instances"]) == 8

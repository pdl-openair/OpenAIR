"""Unit tests for the deterministic materials full-discovery ResearchDesign.

Selecting materials_full_discovery_workflow must produce a fixed five-node
chain (database_search -> candidate_generation -> stability_verification ->
synthesis_execution -> report_generation) with zero LLM planner calls. The
optional nodes (literature_review, synthesis_assessment,
parameter_recommendation, band_gap_calculation) stay dormant.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.modules.materials_deterministic_design import (
    MATERIALS_FULL_DISCOVERY_WORKFLOW_ID,
    PreparedDeterministicMaterialsPlannerAttempt,
    build_materials_deterministic_brief,
    build_materials_deterministic_provider_result,
    compile_materials_full_chain_proposal,
    prepare_deterministic_materials_planner_attempt,
)
from app.modules.materials_workflow_admission import materials_workflow_planning_admission
from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.research_design_service import validate_research_design_proposal
from app.modules.workflow_planning import workflow_planning_bundle

_RAW_INPUT = (
    "找带隙大于 3 eV 的无铅无镉宽带隙氧化物，ehull 尽量低于 50 meV/atom，"
    "从文献调研、数据库检索、候选进化到 DFT 稳定性验证和报告生成走完整流程。"
)

# The five chain nodes.
_DB = "materials:database_search"
_GEN = "materials:candidate_generation"
_DFT = "materials:stability_verification"
_WETLAB = "materials:synthesis_execution"
_REPORT = "materials:report_generation"

# Optional nodes that must stay dormant in the deterministic chain.
_LIT = "materials:literature_review"
_BAND_GAP = "materials:band_gap_calculation"

_CHAIN = {_DB, _GEN, _DFT, _WETLAB, _REPORT}

# Frozen workflow dependencies restricted to the chain nodes: S2 -> S3 ->
# S7 -> S8 -> S9 (workflow v3 also wires the S7 -> S9 direct edge).
_CHAIN_DEPENDENCIES = {
    (_DB, _GEN),
    (_GEN, _DFT),
    (_DFT, _WETLAB),
    (_WETLAB, _REPORT),
    (_DFT, _REPORT),
}

@pytest.fixture(scope="module")
def materials_planning_bundle() -> Any:
    selection = materials_workflow_planning_admission(
        _RAW_INPUT, workflow_id=MATERIALS_FULL_DISCOVERY_WORKFLOW_ID
    )
    source = ProductionDefinitionSource(planning_only=True)
    return workflow_planning_bundle(
        selection=selection, raw_goal=_RAW_INPUT, definition_source=source
    )

def _proposal(bundle: Any) -> dict[str, Any]:
    return compile_materials_full_chain_proposal(
        raw_input=_RAW_INPUT,
        definition_catalog=bundle.definition_catalog,
        workflow_planning_context=bundle.planning_context,
    )

def test_workflow_id_matches_the_registered_identifier() -> None:
    assert MATERIALS_FULL_DISCOVERY_WORKFLOW_ID == "materials_full_discovery_workflow"

def test_brief_plans_chain_nodes_and_omits_optional_nodes(
    materials_planning_bundle: Any,
) -> None:
    brief = build_materials_deterministic_brief(
        raw_input=_RAW_INPUT,
        workflow_planning_context=materials_planning_bundle.planning_context,
    )
    assert brief.research_goal == _RAW_INPUT
    assert all(delta.work_item_scientific_contexts for delta in brief.responsibility_deltas)
    assert all(delta.reuse_candidate_key is None for delta in brief.responsibility_deltas)

def test_proposal_covers_exactly_the_five_chain_nodes(
    materials_planning_bundle: Any,
) -> None:
    proposal = _proposal(materials_planning_bundle)
    definition_ids = {row["definitionId"] for row in proposal["instances"]}
    assert definition_ids == _CHAIN
    assert _LIT not in definition_ids
    assert _BAND_GAP not in definition_ids

def test_proposal_wires_the_chain_dependencies(
    materials_planning_bundle: Any,
) -> None:
    """The chain instances depend on each other exactly along S2 -> S3 -> S7 -> S8 -> S9."""
    proposal = _proposal(materials_planning_bundle)
    definition_by_key = {
        row["instanceKey"]: row["definitionId"] for row in proposal["instances"]
    }
    edges = {
        (
            definition_by_key[dependency["upstreamInstanceKey"]],
            row["definitionId"],
        )
        for row in proposal["instances"]
        for dependency in row["dependencies"]
        if dependency["upstreamInstanceKey"] in definition_by_key
    }
    assert set(definition_by_key.values()) == _CHAIN
    assert edges == _CHAIN_DEPENDENCIES

def test_proposal_is_valid_and_deterministic(materials_planning_bundle: Any) -> None:
    proposal = _proposal(materials_planning_bundle)
    validate_research_design_proposal(proposal)
    repeat = _proposal(materials_planning_bundle)
    assert proposal == repeat

def test_provider_result_is_synthetic(materials_planning_bundle: Any) -> None:
    result = build_materials_deterministic_provider_result(
        raw_input=_RAW_INPUT,
        definition_catalog=materials_planning_bundle.definition_catalog,
        workflow_planning_context=materials_planning_bundle.planning_context,
    )
    assert result.provider_call_ref.startswith("materials-deterministic:")
    assert result.planning_source_usages == ()

def test_prepared_attempt_executes_without_http(materials_planning_bundle: Any) -> None:
    attempt = prepare_deterministic_materials_planner_attempt(
        raw_input=_RAW_INPUT,
        ordinal=1,
        definition_catalog=materials_planning_bundle.definition_catalog,
        workflow_planning_context=materials_planning_bundle.planning_context,
    )
    assert attempt.provider == "deterministic_materials"
    assert attempt.timeout_seconds == 0.0
    result = attempt.execute()
    assert result.proposal["instances"]


def test_prepared_attempt_is_dispatched_by_execute_planner_attempt(
    materials_planning_bundle: Any,
) -> None:
    """The one-shot dispatcher must route the materials attempt to its own execute().

    Regression for the planner_processing_failed incident: prepare_planner_attempt
    returned a PreparedDeterministicMaterialsPlannerAttempt, but execute_planner_attempt's
    isinstance guards only knew about the PVD deterministic attempt, so the materials
    attempt fell through to execute_prepared_attempt and raised AttributeError accessing
    _request_bytes / provider_schema. The dispatcher must recognise the materials variant.
    """
    from app.modules.research_start_planner import execute_planner_attempt

    attempt = prepare_deterministic_materials_planner_attempt(
        raw_input=_RAW_INPUT,
        ordinal=1,
        definition_catalog=materials_planning_bundle.definition_catalog,
        workflow_planning_context=materials_planning_bundle.planning_context,
    )
    assert isinstance(attempt, PreparedDeterministicMaterialsPlannerAttempt)
    result = execute_planner_attempt(attempt)
    assert result.proposal["instances"]

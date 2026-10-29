"""Frozen ownership conformance and controlled legacy/current consumer comparisons."""

from __future__ import annotations

from copy import deepcopy

import pytest
from app.modules.research_start_projection import _planning_key_results
from app.modules.tests.target_output_ownership.audit_support import (
    assemble,
    bundle_for,
    compile_semantic,
    evaluate_controlled_consumers,
    materialize_design,
)


def test_current_frozen_ot_distinct_targets_keep_formal_owner(source):
    bundle = bundle_for(source)
    semantic = assemble(bundle)
    proposal = compile_semantic(semantic, bundle)
    _task, design = materialize_design(proposal, bundle, source)

    assert bundle.frozen_definition.workflow_version == 4
    assert bundle.planning_context["targetOutputSpecs"] == [
        row.model_dump(mode="json", by_alias=True)
        for row in bundle.frozen_definition.definition.target_output_specs
    ]
    for spec in bundle.frozen_definition.definition.target_output_specs:
        target = next(
            row for row in design.coverage_report.targets if row.description == spec.description
        )
        claim = next(
            row for row in design.coverage_report.claims if row.target_id == target.target_id
        )
        owner_ids = {ref.substage_instance_id for ref in claim.instance_refs}
        actual_owners = {
            row.substage_definition_id
            for row in design.instances
            if row.substage_instance_id in owner_ids
        }
        assert actual_owners == {f"opentrajectory:{spec.owner_node_key}"}


@pytest.mark.parametrize("records", ["omitted", "reused"])
def test_conditional_target_has_no_new_work_but_is_not_claimed_by_terminal(source, records):
    bundle = bundle_for(source)
    semantic = assemble(bundle, records=records)
    compile_semantic(semantic, bundle)
    coverage = next(
        row
        for row in semantic.workflow_responsibility_coverage
        if row.responsibility_key == "research_record_establishment"
    )
    assert coverage.work_item_local_keys == []
    assert coverage.coverage_mode == ("reused_result" if records == "reused" else "not_selected")
    assert coverage.reused_result_refs == (
        ["artifact:isolated-prior-records"] if records == "reused" else []
    )
    records_target = bundle.planning_context["targetOutputSpecs"][0]["description"]
    assert not any(records_target in row.target_output_refs for row in semantic.work_items)


def test_one_responsibility_split_is_preserved_without_assigning_its_target_elsewhere(source):
    bundle = bundle_for(source)
    semantic = assemble(bundle, split_owner="evidence_assessment_synthesis")
    proposal = compile_semantic(semantic, bundle)
    assert (
        sum(
            row.definition_id == "opentrajectory:evidence_assessment_synthesis"
            for row in semantic.work_items
        )
        == 2
    )
    assert len(proposal.workflow_responsibility_coverage[1].instance_keys) == 2
    target = bundle.planning_context["targetOutputSpecs"][1]["description"]
    assert {
        row.definition_id for row in semantic.work_items if target in row.target_output_refs
    } == {"opentrajectory:evidence_assessment_synthesis"}


def test_reworded_query_targets_do_not_remove_frozen_deliverables(source):
    bundle = bundle_for(source)
    outputs = [
        "LLM judge 风格偏差的来源记录。",
        "按评测条件分层的证据强度评估。",
        "可确认结论、未知和优先验证建议。",
        "给出一个适合我们评测任务的验证方向。",
    ]
    semantic = assemble(
        bundle,
        outputs=outputs,
        output_owners=[
            "research_record_establishment",
            "evidence_assessment_synthesis",
            "next_research_recommendation",
            "next_research_recommendation",
        ],
    )
    compile_semantic(semantic, bundle)
    assert set(outputs).issubset(semantic.understanding.target_outputs)
    assert set(
        row.description for row in bundle.frozen_definition.definition.target_output_specs
    ).issubset(semantic.understanding.target_outputs)


@pytest.mark.parametrize(
    "workflow_id,version",
    [
        ("opentrajectory_research_workflow", 4),
        ("pvd_process_optimization_workflow", 3),
        ("cs_paper_reproduction_workflow", 7),
    ],
)
def test_current_three_workflows_compile_without_forcing_unselected_work(
    source, workflow_id, version
):
    bundle = bundle_for(source, workflow_id)
    semantic = assemble(bundle, records="omitted")
    proposal = compile_semantic(semantic, bundle)
    _task, design = materialize_design(proposal, bundle, source)
    assert bundle.frozen_definition.workflow_version == version
    assert (
        design.workflow_provenance_ref.content_hash
        == "sha256:" + bundle.frozen_definition.content_hash
    )
    assert len(design.project_completion_requirements) == len(semantic.understanding.target_outputs)
    for row in semantic.workflow_responsibility_coverage:
        if row.requirement == "conditional":
            assert row.coverage_mode == "not_selected"


def test_no_specs_workflow_preserves_extra_query_targets_and_unknown(source):
    bundle = bundle_for(source, "cs_paper_reproduction_workflow")
    assert "targetOutputSpecs" not in bundle.planning_context
    outputs = ["当前复现结论。", "可执行的后续核查方向。"]
    semantic = assemble(bundle, outputs=outputs)
    proposal = compile_semantic(semantic, bundle)
    terminal = next(
        row
        for row in semantic.workflow_responsibility_coverage
        if row.responsibility_key == "reproduction_conclusion_recommendation"
    )
    assert all(not row.target_output_refs for row in semantic.work_items)
    assert not semantic.target_output_owners
    assert proposal.understanding.target_outputs == outputs
    assert any("尚未确认" in note for note in proposal.understanding.risk_notes)
    for index in range(len(outputs)):
        candidate = next(
            row
            for row in proposal.coverage_claim_candidates
            if row.source_path == f"understanding.targetOutputs[{index}]"
        )
        assert not candidate.instance_keys
        assert not candidate.acceptance_criterion_keys

    associated = assemble(
        bundle,
        outputs=outputs,
        output_owners=[terminal.responsibility_key, terminal.responsibility_key],
    )
    explicit_proposal = compile_semantic(associated, bundle)
    _task, design = materialize_design(explicit_proposal, bundle, source)
    assert design.understanding.target_outputs == outputs
    assert all(
        row.target_output_refs == outputs
        for row in associated.work_items
        if row.local_key in terminal.work_item_local_keys
    )


def correct_single_instance_ownership_for_comparison(semantic, bundle):
    """Controlled counterfactual, restricted to explicit unique materialized owners.

    No policy for reuse, omission, split work, or free-text matching is invented.
    This is not a production mapper.
    """
    corrected = deepcopy(semantic)
    for row in corrected.work_items:
        row.target_output_refs = []
    for spec in bundle.frozen_definition.definition.target_output_specs:
        coverage = next(
            row
            for row in corrected.workflow_responsibility_coverage
            if row.responsibility_key == spec.owner_node_key
        )
        assert coverage.coverage_mode == "planned_work"
        assert len(coverage.work_item_local_keys) == 1
        owner = next(
            row for row in corrected.work_items if row.local_key == coverage.work_item_local_keys[0]
        )
        owner.target_output_refs.append(spec.description)
    return corrected


def test_actual_acceptance_and_completion_consumer_exposes_owner_sensitivity(source):
    bundle = bundle_for(source)
    corrected = assemble(bundle)
    current = deepcopy(corrected)
    for row in current.work_items:
        row.target_output_refs = []
    next(
        row for row in current.work_items if row.local_key == "next_research_recommendation"
    ).target_output_refs = list(current.understanding.target_outputs)
    current.target_output_owners = {}

    accepted = {"opentrajectory:next_research_recommendation"}
    current_design, current_state, current_result = evaluate_controlled_consumers(
        current, bundle, source, accepted_definitions=accepted, legacy=True
    )
    correct_design, correct_state, correct_result = evaluate_controlled_consumers(
        corrected, bundle, source, accepted_definitions=accepted
    )

    # Identical controlled durable inputs: only terminal artifacts/acceptance.
    assert (
        current_state["executionState"]["validatedArtifactRefs"]
        == (correct_state["executionState"]["validatedArtifactRefs"])
    )
    assert current_result["canComplete"] is True
    assert correct_result["canComplete"] is False
    assert [row["status"] for row in correct_result["requirementAssessments"]] == [
        "unresolved",
        "unresolved",
        "satisfied",
    ]
    assert all(row["status"] == "satisfied" for row in current_result["requirementAssessments"])
    artifacts_by_definition = {
        row.definition_id: row.expected_artifact_types for row in corrected.work_items
    }
    terminal_artifact = artifacts_by_definition["opentrajectory:next_research_recommendation"][0]
    assert all(
        target.required_artifact_types == [terminal_artifact]
        for target in current_design.coverage_report.targets
        if target.source_type.value == "target_output"
    )
    assert [
        target.required_artifact_types
        for target in correct_design.coverage_report.targets
        if target.source_type.value == "target_output"
    ] == [
        [artifacts_by_definition[f"opentrajectory:{row.owner_node_key}"][0]]
        for row in bundle.frozen_definition.definition.target_output_specs
    ]


def test_current_deterministic_definition_does_not_consume_professional_acceptance(source):
    bundle = bundle_for(source)
    semantic = assemble(bundle)
    _design, _state, result = evaluate_controlled_consumers(
        semantic,
        bundle,
        source,
        accepted_definitions={"opentrajectory:next_research_recommendation"},
        approve=False,
    )
    assert all(
        criterion.judgment_requirement.value == "deterministic"
        for row in _design.instances
        for criterion in row.acceptance_criteria
    )
    assert result["canComplete"] is False
    assert [row["status"] for row in result["requirementAssessments"]] == [
        "unresolved",
        "unresolved",
        "satisfied",
    ]


def test_all_explicit_owners_accepted_complete_both_controlled_designs(source):
    bundle = bundle_for(source)
    semantic = assemble(bundle)
    accepted = {
        f"opentrajectory:{row.owner_node_key}"
        for row in bundle.frozen_definition.definition.target_output_specs
    }
    for candidate in (semantic, correct_single_instance_ownership_for_comparison(semantic, bundle)):
        _design, _state, result = evaluate_controlled_consumers(
            candidate, bundle, source, accepted_definitions=accepted
        )
        assert result["canComplete"] is True


def test_public_planning_projection_displays_proposal_and_does_not_restore_authority(source):
    bundle = bundle_for(source)
    semantic = assemble(
        bundle, outputs=["当前问题的可信结论。"], output_owners=["next_research_recommendation"]
    )
    proposal = compile_semantic(semantic, bundle)
    public = _planning_key_results({"proposal": proposal.model_dump(mode="json", by_alias=True)})
    assert public["targetOutputs"] == proposal.understanding.target_outputs
    assert set(
        row.description for row in bundle.frozen_definition.definition.target_output_specs
    ).issubset(public["targetOutputs"])

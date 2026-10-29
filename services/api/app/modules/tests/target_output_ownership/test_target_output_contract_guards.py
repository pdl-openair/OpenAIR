"""Frozen ownership conformance and controlled legacy/current consumer comparisons."""

from __future__ import annotations

import pytest
from app.modules.research_design_planner_assembly import build_adaptive_workflow_semantics
from app.modules.tests.target_output_ownership.audit_support import (
    QUERY,
    assemble,
    brief_for,
    bundle_for,
    compile_semantic,
    materialize_design,
)


def test_unassociated_reworded_query_target_remains_explicitly_unresolved(source):
    bundle = bundle_for(source)
    output = "Changed wording of the evidence comparison."
    semantic = assemble(bundle, outputs=[output])
    assert output in semantic.understanding.target_outputs
    assert output not in semantic.target_output_owners
    assert all(output not in row.target_output_refs for row in semantic.work_items)
    assert any(output in note for note in semantic.understanding.risk_notes)
    proposal = compile_semantic(semantic, bundle)
    _task, design = materialize_design(proposal, bundle, source)
    target = next(row for row in design.coverage_report.targets if row.description == output)
    claim = next(row for row in design.coverage_report.claims if row.target_id == target.target_id)
    assert not claim.instance_refs
    assert not claim.acceptance_criterion_refs
    for spec in bundle.planning_context["targetOutputSpecs"]:
        assert semantic.target_output_owners[spec["description"]] == spec["ownerNodeKey"]


def test_conflicting_work_item_cannot_override_frozen_owner_in_compiler(source):
    bundle = bundle_for(source)
    semantic = assemble(bundle)
    output = bundle.planning_context["targetOutputSpecs"][0]["description"]
    for row in semantic.work_items:
        if output in row.target_output_refs:
            row.target_output_refs.remove(output)
    semantic.work_items[-1].target_output_refs.append(output)
    with pytest.raises(ValueError, match="conflict"):
        compile_semantic(semantic, bundle)


def test_direct_proposal_cannot_override_frozen_owner(source):
    bundle = bundle_for(source)
    proposal = compile_semantic(assemble(bundle), bundle)
    proposal.target_output_owners["understanding.targetOutputs[0]"] = "next_research_recommendation"
    with pytest.raises(ValueError, match="frozen Workflow"):
        materialize_design(proposal, bundle, source)


def test_provider_decode_reaches_the_same_frozen_ownership_compiler(source):
    import json

    import httpx
    from app.modules.research_design_workflow_semantic_provider import (
        WorkflowSemanticProviderConfig,
        prepare_hybrid_workflow_planner_attempt,
    )

    bundle = bundle_for(source)
    brief, _sources = brief_for(bundle)
    prepared = prepare_hybrid_workflow_planner_attempt(
        raw_input=QUERY,
        ordinal=1,
        definition_catalog=bundle.definition_catalog,
        workflow_planning_context=bundle.planning_context,
        provider_config=WorkflowSemanticProviderConfig(
            provider="huiyan_chat_completions",
            model="isolated-test",
            endpoint="https://provider.test",
            timeout_seconds=1,
        ),
    )

    class Transport:
        def post(self, *args, **kwargs):
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://provider.test"),
                json={
                    "id": "isolated-call",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(brief.model_dump(mode="json", by_alias=True))
                            }
                        }
                    ],
                },
            )

    result = prepared.execute(transport=Transport())
    assert result.proposal["targetOutputOwners"]["understanding.targetOutputs[0]"] == (
        "research_record_establishment"
    )
    assert result.provider_call_ref == "isolated-call"


def test_shared_contract_fixture_matches_additive_formal_models():
    import json

    from app.models.research_orchestration import ProjectCompletionRequirement
    from app.modules.repo_paths import repo_root
    from app.modules.target_output_completion import TargetOutputReuseAcceptance

    fixture = json.loads(
        (
            repo_root()
            / "packages/shared/test-fixtures/workflow-target-output-ownership-valid.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        ProjectCompletionRequirement.model_validate(
            fixture["projectCompletionRequirement"]
        ).workflow_responsibility_key
        == "records"
    )
    assert (
        TargetOutputReuseAcceptance.model_validate(fixture["reuseAcceptance"]).acceptanceArtifactRef
        == "artifact:current-review"
    )


@pytest.mark.parametrize(
    "case", ["unknown_work", "unknown_target", "unowned", "duplicate", "incomplete"]
)
def test_compiler_is_authority_for_internal_target_requirement_graph(source, case):
    from app.models.research_planner_semantic import PlannerSemanticTargetRequirementV2
    from app.modules.research_design_compiler import ResearchDesignCompilerError

    bundle = bundle_for(source)
    semantic = assemble(bundle)
    pairs = [
        (row.local_key, output) for row in semantic.work_items for output in row.target_output_refs
    ]
    requirements = [
        PlannerSemanticTargetRequirementV2(
            workItemLocalKey=work,
            targetOutputRef=output,
            completionMode="artifact_presence",
        )
        for work, output in pairs
    ]
    if case == "unknown_work":
        requirements[0].work_item_local_key = "unknown-work"
    elif case == "unknown_target":
        requirements[0].target_output_ref = "unknown-output"
    elif case == "unowned":
        requirements[0].work_item_local_key = pairs[-1][0]
    elif case == "duplicate":
        requirements.append(requirements[0])
    else:
        requirements.pop()
    semantic.target_requirements = requirements
    with pytest.raises(ResearchDesignCompilerError):
        compile_semantic(semantic, bundle)


@pytest.mark.parametrize("associate_empty", [False, True])
def test_provider_local_output_numbers_survive_duplicate_and_empty_text_normalization(
    source, associate_empty
):
    from app.modules.research_design_workflow_semantics import parse_workflow_semantic_delta

    bundle = bundle_for(source)
    brief, sources = brief_for(bundle)
    raw = brief.model_dump(mode="json", by_alias=True)
    raw["targetOutputs"] = ["当前来源记录", "", "当前证据比较", "当前来源记录"]
    raw["responsibilityDeltas"][0]["targetOutputNumbers"] = [1, 4]
    raw["responsibilityDeltas"][1]["targetOutputNumbers"] = [2 if associate_empty else 3]
    parsed = parse_workflow_semantic_delta(raw)
    semantic = build_adaptive_workflow_semantics(
        raw_input=QUERY,
        definition_catalog=bundle.definition_catalog,
        workflow_planning_context=bundle.planning_context,
        brief=parsed,
        planning_sources=sources,
    )
    assert semantic.target_output_owners["当前来源记录"] == "research_record_establishment"
    assert "当前证据比较" in semantic.understanding.target_outputs
    if associate_empty:
        assert "当前证据比较" not in semantic.target_output_owners
        assert all("当前证据比较" not in row.target_output_refs for row in semantic.work_items)
        assert any("目标引用" in note and "2" in note for note in semantic.understanding.risk_notes)
    else:
        assert semantic.target_output_owners["当前证据比较"] == "evidence_assessment_synthesis"
    compile_semantic(semantic, bundle)


@pytest.mark.parametrize("records", ["planned", "omitted", "reused"])
def test_current_design_revision_preserves_target_owners_and_non_work_coverage(source, records):
    from app.modules.research_design_agent_revision import (
        _proposal_from_current_design,
        _validate_targeted_patch,
    )

    bundle = bundle_for(source)
    proposal = compile_semantic(assemble(bundle, records=records), bundle)
    task, design = materialize_design(proposal, bundle, source)
    task = task.model_copy(update={"research_design_versions": [design]})
    patch = _validate_targeted_patch(
        {
            "goal": design.goal,
            "workItems": [
                {"substageInstanceId": row.substage_instance_id, "objective": row.objective}
                for row in design.instances
            ],
        },
        design,
    )
    revised = _proposal_from_current_design(design, patch)
    assert revised.target_output_owners == proposal.target_output_owners
    assert [row.coverage_mode for row in revised.workflow_responsibility_coverage] == [
        row.coverage_mode for row in proposal.workflow_responsibility_coverage
    ]
    _task, materialized = materialize_design(revised, bundle, source, task=task)
    assert materialized.project_completion_requirements == design.project_completion_requirements

"""Completion follows all necessary contributions and current accepted facts."""

from copy import deepcopy

import pytest
from app.modules.research_design_agent_revision import (
    _proposal_from_current_design,
    _validate_targeted_patch,
)
from app.modules.research_finalization import record_scientific_state_from_durable_facts

from .controlled_facts import add_controlled_durable_facts
from .support import NOW, build, bundle_for, confirm, decode, evaluate, real_payload


def controlled_state(source, *, accepted="all", payload=None, omitted_objectives=()):
    bundle = bundle_for(source)
    service, repository, design = build(decode(payload or real_payload(), bundle), bundle, source)
    task, design = confirm(service, repository, design)
    definitions = {row.substage_definition_id for row in design.instances}
    if accepted == "last":
        definitions = {"opentrajectory:next_research_recommendation"}
    elif accepted == "partial":
        definitions.remove("opentrajectory:key_unknowns_candidate_explanations")
    task = add_controlled_durable_facts(
        task, design, accepted_definitions=definitions, omitted_objectives=omitted_objectives
    )
    _snapshot, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    return design, state


@pytest.mark.parametrize("accepted,complete", [("last", False), ("partial", False), ("all", True)])
def test_composite_needs_all_current_contributions(source, accepted, complete):
    design, state = controlled_state(source, accepted=accepted)
    result = evaluate(design, state)
    target = next(
        row
        for row in design.coverage_report.targets
        if row.description == real_payload()["targetOutputs"][4]
    )
    requirement = next(
        row
        for row in design.project_completion_requirements
        if row.target_output_ref == target.source_path
    )
    assessment = next(
        row
        for row in result["requirementAssessments"]
        if row["completionRequirementId"] == requirement.completion_requirement_id
    )
    assert assessment["status"] == ("satisfied" if complete else "unresolved")
    assert result["canComplete"] is complete


def test_multi_focus_subgoal_requires_its_current_accepted_result(source):
    payload = real_payload()
    row = payload["responsibilityDeltas"][4]
    row.pop("currentProjectWorkContext")
    row["workItemScientificContexts"] = ["判断长度的因果贡献。", "判断完成式表达的因果贡献。"]
    design, state = controlled_state(source, payload=payload)
    assert evaluate(design, state)["canComplete"] is True
    split = [
        item
        for item in design.instances
        if item.substage_definition_id == "opentrajectory:key_unknowns_candidate_explanations"
    ]
    assert len(split) == 1
    assert all(context in split[0].objective for context in row["workItemScientificContexts"])
    partial_design, partial_state = controlled_state(
        source, payload=payload, omitted_objectives=[split[0].objective]
    )
    assert evaluate(partial_design, partial_state)["canComplete"] is False


def nonwork_payload(bundle, *, reuse):
    rows = sorted(
        [
            *bundle.planning_context["coreResponsibilities"],
            *bundle.planning_context["conditionalResponsibilities"],
        ],
        key=lambda row: row["displayOrder"],
    )
    payload = {"goal": "依据现有来源判断当前问题。", "responsibilityDeltas": []}
    for index, row in enumerate(rows, 1):
        if row["nodeKey"] == "research_record_establishment":
            if reuse:
                payload["responsibilityDeltas"].append(
                    {
                        "responsibilityChoice": f"responsibility_{index}",
                        "reuseCandidateKey": "planning_source_1",
                    }
                )
        else:
            payload["responsibilityDeltas"].append(
                {
                    "responsibilityChoice": f"responsibility_{index}",
                    "currentProjectWorkContext": f"核查{row['nodeKey']}。",
                }
            )
    if reuse:
        payload["planningSourceUses"] = [
            {
                "sourceChoice": "planning_source_1",
                "adoptedUnitNumbers": [1],
                "notAdoptedUnitNumbers": [],
                "revalidationUnitNumbers": [],
            }
        ]
    return payload


@pytest.mark.parametrize(
    "mutation", ["pending", "current", "version", "source", "artifact", "not_approved"]
)
def test_reuse_requires_current_correct_accepted_source(source, mutation):
    from app.modules.research_design_service import validate_research_design_proposal
    from app.modules.research_design_workflow_semantic_provider import (
        WorkflowSemanticProviderConfig,
        prepare_hybrid_workflow_planner_attempt,
    )

    from .support import QUERY, RecordedTransport

    bundle = bundle_for(source)
    prepared = prepare_hybrid_workflow_planner_attempt(
        raw_input=QUERY,
        ordinal=1,
        definition_catalog=bundle.definition_catalog,
        workflow_planning_context=bundle.planning_context,
        planning_sources=[
            {
                "sourceChoice": "planning_source_1",
                "sourceArtifactRef": "artifact:pv2-prior",
                "sourceKind": "research_package",
                "sourceId": "pv2-prior",
                "sourceVersion": "1.0",
                "sourceContentHash": "sha256:" + "c" * 64,
                "name": "Offline software control",
                "units": [
                    {
                        "unitNumber": 1,
                        "sourcePath": "/results/0",
                        "category": "reference_result",
                        "content": "Supplied software test premise.",
                        "supportingSources": [],
                    }
                ],
            }
        ],
        provider_config=WorkflowSemanticProviderConfig(
            provider="huiyan_chat_completions",
            model="offline-control",
            endpoint="https://pv2.invalid",
            timeout_seconds=1,
        ),
    )
    proposal = validate_research_design_proposal(
        prepared.execute(transport=RecordedTransport(nonwork_payload(bundle, reuse=True))).proposal
    )
    from .evidence import record

    record("input", nonwork_payload(bundle, reuse=True))
    record(
        "prepared_decode_receiver_assembly_compiler",
        proposal.model_dump(mode="json", by_alias=True),
    )
    service, repository, design = build(proposal, bundle, source)
    task, design = confirm(service, repository, design)
    task = add_controlled_durable_facts(
        task, design, accepted_definitions={row.substage_definition_id for row in design.instances}
    )
    _snapshot, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    artifact = state["executionState"]["validatedArtifactRefs"][0]
    requirement = next(
        row
        for row in design.project_completion_requirements
        if row.workflow_responsibility_key == "research_record_establishment"
    )
    acceptance = {
        "researchDesignId": design.research_design_id,
        "researchDesignVersion": design.version,
        "targetOutputRef": requirement.target_output_ref,
        "responsibilityKey": "research_record_establishment",
        "reusedResultRefs": ["artifact:pv2-prior"],
        "acceptanceArtifactRef": artifact,
        "verdict": "accepted",
    }
    if mutation == "version":
        acceptance["researchDesignVersion"] += 1
    elif mutation == "source":
        acceptance["reusedResultRefs"] = ["artifact:wrong-source"]
    elif mutation == "artifact":
        acceptance["acceptanceArtifactRef"] = "artifact:not-validated"
    elif mutation == "not_approved":
        state["executionState"]["approvedArtifactRefs"] = []
    state["scientificFacts"]["targetOutputReuseAcceptances"] = (
        [] if mutation == "pending" else [acceptance]
    )
    state["sourceRefs"] = list(set([*state["sourceRefs"], artifact]))
    result = evaluate(design, state)
    assessment = next(
        row
        for row in result["requirementAssessments"]
        if row["completionRequirementId"] == requirement.completion_requirement_id
    )
    assert assessment["fulfillmentStatus"] == (
        "reuse_accepted" if mutation == "current" else "reuse_pending"
    )
    assert result["canComplete"] is (mutation == "current")


def test_conditional_not_applicable_is_not_forced_to_execute(source):
    bundle = bundle_for(source)
    service, repository, design = build(
        decode(nonwork_payload(bundle, reuse=False), bundle), bundle, source
    )
    task, design = confirm(service, repository, design)
    assert not any(
        row.substage_definition_id == "opentrajectory:research_record_establishment"
        for row in design.instances
    )
    task = add_controlled_durable_facts(
        task, design, accepted_definitions={row.substage_definition_id for row in design.instances}
    )
    _snapshot, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    result = evaluate(design, state)
    assert any(
        row["fulfillmentStatus"] == "not_applicable" for row in result["requirementAssessments"]
    )
    assert result["canComplete"] is True


def test_formal_revision_preserves_composite_and_old_version(source):
    bundle = bundle_for(source)
    service, repository, design = build(decode(real_payload(), bundle), bundle, source)
    old = deepcopy(design.model_dump(mode="json", by_alias=True))
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
    proposal = _proposal_from_current_design(design, patch)
    revised = service._build_design(
        repository.task, proposal, "pv2-revision", owner_id="pv2", workspace_id="pv2"
    )
    assert revised.version == 2
    target = real_payload()["targetOutputs"][4]
    revised_target = next(
        row for row in revised.coverage_report.targets if row.description == target
    )
    claim = next(
        row for row in revised.coverage_report.claims if row.target_id == revised_target.target_id
    )
    assert len(claim.instance_refs) == 2
    assert repository.task.research_design_versions[0].model_dump(mode="json", by_alias=True) == old


def test_old_scientific_state_cannot_complete_current_revision(source):
    from app.modules.research_completion_decision import ProjectCompletionAuthorityError

    design, state = controlled_state(source)
    assert evaluate(design, state)["canComplete"] is True
    stale = deepcopy(state)
    stale["researchDesignVersion"] = design.version + 1
    with pytest.raises(ProjectCompletionAuthorityError, match="completion_state_design_mismatch"):
        evaluate(design, stale)

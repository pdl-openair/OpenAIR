"""Historical drafts must survive the real command/save boundary without false completion."""

import json

import pytest
from app.modules.research_completion_decision import expected_project_completion_evaluation
from app.modules.research_design_service import ResearchDesignRevisionSaveUnavailable
from app.modules.research_finalization import record_scientific_state_from_durable_facts
from app.modules.tests.target_output_ownership.audit_support import (
    NOW,
    add_controlled_durable_facts,
    brief_for,
    bundle_for,
    materialize_design,
)
from app.modules.tests.target_output_ownership.legacy_revision_support import (
    activate_revision,
    legacy_task,
    revise,
)


def assert_frozen_coverage(design, bundle):
    report = design.coverage_report
    coverage = {row.responsibility_key: row for row in design.workflow_responsibility_coverage}
    requirements = {row.description: row for row in design.project_completion_requirements}
    for spec in bundle.planning_context["targetOutputSpecs"]:
        requirement = requirements[spec["description"]]
        assert requirement.workflow_responsibility_key == spec["ownerNodeKey"]
        target = next(
            row for row in report.targets if row.source_path == requirement.target_output_ref
        )
        claim = next(row for row in report.claims if row.target_id == target.target_id)
        assert claim.instance_refs == coverage[spec["ownerNodeKey"]].instance_refs
        expected_ids = {row.substage_instance_id for row in claim.instance_refs}
        criteria = {
            row.criterion_id
            for instance in design.instances
            if instance.substage_instance_id in expected_ids
            for row in instance.acceptance_criteria
            if target.target_id in row.coverage_target_ids
        }
        assert set(claim.acceptance_criterion_refs) == criteria


@pytest.mark.parametrize("records", ["planned", "omitted", "reused"])
def test_actual_legacy_producer_command_save_and_completion(source, records):
    bundle = bundle_for(source)
    repository, old = legacy_task(source, bundle, records=records)
    before = old.model_dump(mode="json", by_alias=True)
    assert not any(row.workflow_responsibility_key for row in old.project_completion_requirements)
    task, design = revise(repository, source)
    assert len(repository.writes) == 2
    assert design.version == 2 and design.status.value == "draft"
    assert old.model_dump(mode="json", by_alias=True) == before
    saved_old = task.research_design_versions[0].model_dump(mode="json", by_alias=True)
    assert saved_old == {**before, "status": "superseded"}
    assert design.understanding == old.understanding
    assert design.workflow_responsibility_coverage == old.workflow_responsibility_coverage or [
        (row.responsibility_key, row.coverage_mode, row.reused_result_refs)
        for row in design.workflow_responsibility_coverage
    ] == [
        (row.responsibility_key, row.coverage_mode, row.reused_result_refs)
        for row in old.workflow_responsibility_coverage
    ]
    assert_frozen_coverage(design, bundle)
    task, design = activate_revision(task, design)
    task = add_controlled_durable_facts(
        task, design, accepted_definitions={"opentrajectory:next_research_recommendation"}
    )
    _, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    result = expected_project_completion_evaluation(design, state)
    assert result["canComplete"] is False
    assessments = result["requirementAssessments"]
    assert assessments[1]["status"] == "unresolved"
    assert assessments[0]["status"] == ("satisfied" if records == "omitted" else "unresolved")
    if records != "planned":
        assert (
            assessments[0]["fulfillmentStatus"]
            == {"omitted": "not_applicable", "reused": "reuse_pending"}[records]
        )


@pytest.mark.parametrize("corruption", [None, "missing_extra", "conflicting_frozen_owner"])
def test_extra_query_target_uses_existing_scientific_planner_and_original_graph(
    source, monkeypatch, corruption
):
    from app.modules.research_design_workflow_semantic_provider import (
        PreparedHybridWorkflowPlannerAttempt,
    )

    bundle = bundle_for(source)
    canonical = [row["description"] for row in bundle.planning_context["targetOutputSpecs"]]
    outputs = [*canonical, "按评测条件分层核查篇幅偏差。"]
    repository, old = legacy_task(source, bundle, outputs=outputs)
    brief, _ = brief_for(
        bundle,
        outputs=outputs,
        output_owners=[
            "research_record_establishment",
            "evidence_assessment_synthesis",
            "next_research_recommendation",
            "evidence_assessment_synthesis",
        ],
    )
    if corruption == "missing_extra":
        brief, _ = brief_for(bundle)
    elif corruption == "conflicting_frozen_owner":
        brief, _ = brief_for(
            bundle,
            outputs=outputs,
            output_owners=[
                "evidence_assessment_synthesis",
                "evidence_assessment_synthesis",
                "next_research_recommendation",
                "evidence_assessment_synthesis",
            ],
        )
    calls = []
    original_execute = PreparedHybridWorkflowPlannerAttempt.execute

    def execute(prepared, **_kwargs):
        calls.append(prepared)
        body = {
            "id": "isolated-scientific-ownership",
            "choices": [
                {"message": {"content": json.dumps(brief.model_dump(mode="json", by_alias=True))}}
            ],
        }
        import httpx

        class Transport:
            def post(self, *args, **kwargs):
                return httpx.Response(
                    200, request=httpx.Request("POST", "https://isolated.invalid"), json=body
                )

        return original_execute(prepared, transport=Transport())

    monkeypatch.setattr(PreparedHybridWorkflowPlannerAttempt, "execute", execute)
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://isolated.invalid")
    if corruption is not None:
        with pytest.raises(ResearchDesignRevisionSaveUnavailable):
            revise(repository, source)
        assert len(repository.task.research_design_versions) == 1
        return
    task, design = revise(repository, source)
    assert len(calls) == 1
    assert "target_output_numbers" in str(
        calls[0].semantic_attempt.provider_schema
    ) or "targetOutputNumbers" in str(calls[0].semantic_attempt.provider_schema)
    assert [row.substage_instance_id for row in design.instances] == [
        row.substage_instance_id for row in old.instances
    ]
    assert_frozen_coverage(design, bundle)
    extra = design.project_completion_requirements[-1]
    assert extra.description == outputs[-1]
    assert extra.workflow_responsibility_key == "evidence_assessment_synthesis"
    assert "isolated-scientific-ownership" in task.planning_attempts[-1].planner_provider_call_ref


def test_different_frozen_admission_cannot_restore_historical_ownership(source):
    bundle = bundle_for(source)
    repository, _ = legacy_task(source, bundle)
    # A genuine other admission: different registered Workflow, not malformed hashes.
    conflicting = bundle_for(source, "pvd_process_optimization_workflow").admission
    repository.admission = conflicting
    with pytest.raises(ResearchDesignRevisionSaveUnavailable, match="研究流程不一致"):
        revise(repository, source)
    assert len(repository.task.research_design_versions) == 1


def test_current_command_keeps_recovered_owners_without_replanning(source, monkeypatch):
    from app.modules import research_design_agent_revision as revision

    bundle = bundle_for(source)
    repository, _ = legacy_task(source, bundle)
    _, first = revise(repository, source)

    def never(*_args):
        pytest.fail("Current ownership must not be reconstructed by a model")

    monkeypatch.setattr(revision, "_legacy_query_associations", never)
    task, second = revise(repository, source)
    assert second.version == 3
    assert second.project_completion_requirements == first.project_completion_requirements
    assert task.planning_attempts[-1].user_input_snapshot.base_research_design_ref.version == 2
    assert_frozen_coverage(second, bundle)


@pytest.mark.parametrize("owner", ["unknown_responsibility", "next_research_recommendation"])
def test_partial_or_forged_current_owner_is_not_repaired_as_history(source, owner):
    from app.modules.research_design_agent_revision import (
        _proposal_from_current_design,
        _validate_targeted_patch,
    )

    bundle = bundle_for(source)
    repository, old = legacy_task(source, bundle)
    requirements = list(old.project_completion_requirements)
    requirements[0] = requirements[0].model_copy(update={"workflow_responsibility_key": owner})
    forged = old.model_copy(update={"project_completion_requirements": requirements})
    patch = _validate_targeted_patch(
        {
            "goal": old.goal,
            "workItems": [
                {"substageInstanceId": row.substage_instance_id, "objective": row.objective}
                for row in old.instances
            ],
        },
        old,
    )
    proposal = _proposal_from_current_design(
        forged,
        patch,
        workflow_admission=repository.admission,
        definition_source=source,
        query_owner_provider=lambda _: pytest.fail(
            "Partial current authority must not enter compatibility"
        ),
    )
    with pytest.raises(ValueError):
        materialize_design(proposal, bundle, source, task=repository.task)


def test_old_version_reuse_acceptance_does_not_complete_revised_target(source):
    bundle = bundle_for(source)
    repository, old = legacy_task(source, bundle, records="reused")
    task, design = revise(repository, source)
    task, design = activate_revision(task, design)
    task = add_controlled_durable_facts(
        task, design, accepted_definitions={row.substage_definition_id for row in design.instances}
    )
    _, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    artifact = state["executionState"]["validatedArtifactRefs"][0]
    stale = {
        "researchDesignId": old.research_design_id,
        "researchDesignVersion": old.version,
        "targetOutputRef": design.project_completion_requirements[0].target_output_ref,
        "responsibilityKey": "research_record_establishment",
        "reusedResultRefs": ["artifact:isolated-prior-records"],
        "acceptanceArtifactRef": artifact,
        "verdict": "accepted",
    }
    state["scientificFacts"]["targetOutputReuseAcceptances"] = [stale]
    state["sourceRefs"] = list(set([*state["sourceRefs"], artifact]))
    result = expected_project_completion_evaluation(design, state)
    assert result["canComplete"] is False
    assert result["requirementAssessments"][0]["fulfillmentStatus"] == "reuse_pending"
    # Identical controlled facts are accepted only when scoped to the actual new version.
    stale["researchDesignVersion"] = design.version
    assert expected_project_completion_evaluation(design, state)["canComplete"] is True

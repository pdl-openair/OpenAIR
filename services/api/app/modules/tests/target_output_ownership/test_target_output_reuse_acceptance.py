"""Frozen ownership conformance and controlled legacy/current consumer comparisons."""

from __future__ import annotations

import pytest
from app.modules.research_completion_decision import expected_project_completion_evaluation
from app.modules.research_finalization import record_scientific_state_from_durable_facts
from app.modules.tests.target_output_ownership.audit_support import (
    NOW,
    assemble,
    bundle_for,
    checked_reuse_result,
    evaluate_controlled_consumers,
    reused_task_and_acceptance,
)


def test_not_applicable_target_is_retained_and_never_requires_optional_execution(source):
    bundle = bundle_for(source)
    semantic = assemble(bundle, records="omitted")
    accepted = {row.definition_id for row in semantic.work_items}
    design, state, result = evaluate_controlled_consumers(
        semantic,
        bundle,
        source,
        accepted_definitions=accepted,
    )
    assert result["canComplete"] is True
    assert result["requirementAssessments"][0]["fulfillmentStatus"] == "not_applicable"
    assert result["requirementAssessments"][0]["acceptanceCriterionRefs"] == []
    assert len(design.project_completion_requirements) == 3
    assert not any(
        row["substageInstanceId"] == "research_record_establishment"
        for row in state["executionState"]["substageCompletion"]
    )


def test_adoption_without_current_project_acceptance_keeps_reuse_pending(source):
    task, design, _acceptance = reused_task_and_acceptance(source)
    _task, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    result = expected_project_completion_evaluation(design, state)
    assert result["canComplete"] is False
    assert result["requirementAssessments"][0]["fulfillmentStatus"] == "reuse_pending"


@pytest.mark.parametrize("approve", [False, True])
def test_actual_declared_fact_projection_requires_durable_acceptance_for_reuse(source, approve):
    import json

    from app.models.research_orchestration import ResearchTaskV12
    from app.modules.scientific_state_fact_projection import (
        materialize_scientific_state_projection,
        project_validated_structured_result,
        validate_scientific_state_projection_declaration,
    )
    from app.modules.target_output_completion import TargetOutputReuseAcceptance

    task, design, acceptance = reused_task_and_acceptance(source, approve=approve)
    declaration = validate_scientific_state_projection_declaration(
        {
            "contract_version": 2,
            "materializer_ref": "research:declared-scientific-facts:v1",
            "facts": [{"fact_key": "targetOutputReuseAcceptances", "source_field": "acceptances"}],
        },
        output_schema={
            "type": "object",
            "required": ["acceptances"],
            "properties": {
                "acceptances": {
                    "type": "array",
                    "items": TargetOutputReuseAcceptance.model_json_schema(),
                }
            },
        },
    )
    projected = project_validated_structured_result(
        json.dumps({"acceptances": [acceptance]}),
        declaration,
    )
    payload = materialize_scientific_state_projection(
        task_payload=task.model_dump(mode="json", by_alias=True),
        declaration=declaration,
        projected_facts=projected,
        source_artifact_id=acceptance["acceptanceArtifactRef"],
        research_design_version=design.version,
        occurred_at=NOW,
    )
    task = ResearchTaskV12.model_validate(payload)
    _task, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    result = expected_project_completion_evaluation(design, state)
    assert result["canComplete"] is approve
    assert result["requirementAssessments"][0]["fulfillmentStatus"] == (
        "reuse_accepted" if approve else "reuse_pending"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("researchDesignVersion", 2),
        ("researchDesignId", "historical-design"),
        ("targetOutputRef", "understanding.targetOutputs[2]"),
        ("responsibilityKey", "evidence_assessment_synthesis"),
        ("reusedResultRefs", ["artifact:unrelated-source"]),
        ("acceptanceArtifactRef", "artifact:unrelated-acceptance"),
    ],
)
def test_reuse_acceptance_for_another_scope_or_source_is_rejected(source, field, value):
    from app.modules.research_scientific_state_materialization import (
        materialize_declared_scientific_facts,
    )

    task, design, acceptance = reused_task_and_acceptance(source)
    artifact_ref = acceptance["acceptanceArtifactRef"]
    acceptance[field] = value
    with pytest.raises(ValueError):
        materialize_declared_scientific_facts(
            task_payload=task.model_dump(mode="json", by_alias=True),
            projected_facts={"targetOutputReuseAcceptances": [acceptance]},
            source_artifact_id=artifact_ref,
            research_design_version=design.version,
            occurred_at=NOW,
        )


@pytest.mark.parametrize("approve,review_inputs", [(False, False), (True, False), (True, True)])
def test_formal_passed_result_seam_projects_scoped_reuse_judgment(source, approve, review_inputs):
    import json

    import jsonschema
    from app.models.research_orchestration import ResearchTaskV12
    from app.modules.repo_paths import repo_root
    from app.modules.target_output_reuse_materialization import (
        materialize_checked_agent_scientific_facts,
    )

    payload, design, content = checked_reuse_result(
        source, approve=approve, review_inputs=review_inputs
    )
    schema = json.loads(
        (
            repo_root()
            / "schemas/agents/artifacts/general_research_reasoning_artifact_v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.validate(json.loads(content), schema)
    payload = materialize_checked_agent_scientific_facts(
        task_payload=payload,
        result_id="isolated-checked-reuse",
        validated_artifact_content=content,
        occurred_at=NOW,
    )
    task = ResearchTaskV12.model_validate(payload)
    _task, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    result = expected_project_completion_evaluation(design, state)
    assert result["canComplete"] is approve
    assert result["requirementAssessments"][0]["fulfillmentStatus"] == (
        "reuse_accepted" if approve else "reuse_pending"
    )
    assert bool(task.results[0].scientific_state_versions) is approve


def test_reuse_judgment_requires_materials_actually_supplied_to_work(source):
    from app.modules.target_output_reuse_materialization import (
        materialize_checked_agent_scientific_facts,
    )

    payload, _design, content = checked_reuse_result(source)
    for row in payload["workUnits"]:
        row["inputArtifactRefs"] = []
    with pytest.raises(ValueError, match="not supplied"):
        materialize_checked_agent_scientific_facts(
            task_payload=payload,
            result_id="isolated-checked-reuse",
            validated_artifact_content=content,
            occurred_at=NOW,
        )


@pytest.mark.parametrize(
    "alteration", ["failed_result", "historical_work", "wrong_artifact_source"]
)
def test_ineligible_result_never_produces_current_reuse_acceptance(source, alteration):
    from app.modules.target_output_reuse_materialization import (
        materialize_checked_agent_scientific_facts,
    )

    payload, _design, content = checked_reuse_result(source)
    if alteration == "failed_result":
        payload["results"][0]["checkStatus"] = "failed"
    elif alteration == "historical_work":
        next(
            row
            for row in payload["workUnits"]
            if row["workUnitId"] == payload["results"][0]["workUnitId"]
        )["researchDesignVersion"] = 99
    else:
        next(
            row
            for row in payload["artifacts"]
            if row["artifactId"] == payload["results"][0]["artifactRefs"][0]
        )["sourceResultId"] = "unrelated-result"
    result = materialize_checked_agent_scientific_facts(
        task_payload=payload,
        result_id="isolated-checked-reuse",
        validated_artifact_content=content,
        occurred_at=NOW,
    )
    assert not any(
        "targetOutputReuseAcceptances" in row["scientificFacts"]
        for row in result.get("scientificStates", [])
    )


def test_later_empty_projection_preserves_previous_accepted_target(source):
    from app.modules.research_scientific_state_materialization import (
        materialize_declared_scientific_facts,
    )

    task, design, acceptance = reused_task_and_acceptance(source)
    kwargs = {
        "source_artifact_id": acceptance["acceptanceArtifactRef"],
        "research_design_version": design.version,
        "occurred_at": NOW,
    }
    payload = materialize_declared_scientific_facts(
        task_payload=task.model_dump(mode="json", by_alias=True),
        projected_facts={"targetOutputReuseAcceptances": [acceptance]},
        **kwargs,
    )
    later = materialize_declared_scientific_facts(
        task_payload=payload,
        projected_facts={"targetOutputReuseAcceptances": []},
        **kwargs,
    )
    assert later["scientificStates"][-1]["scientificFacts"]["targetOutputReuseAcceptances"] == [
        acceptance
    ]


def test_reused_material_and_acceptance_instruction_reach_existing_synthesis_work(source):
    semantic = assemble(bundle_for(source), records="reused")
    terminal = semantic.work_items[-1]
    assert "artifact:isolated-prior-records" in terminal.input_refs
    assert "targetOutputReuseJudgments" in terminal.objective
    assert "材料缺失或不足时不接受" in terminal.objective


def test_pending_reuse_judgment_materializes_only_after_real_review_acceptance(source):
    from app.models.research_orchestration import ResearchTaskV12
    from app.modules.research_orchestration_repository import _materialize_checked_result_projection
    from app.modules.target_output_reuse_materialization import (
        materialize_checked_agent_scientific_facts,
    )

    payload, design, content = checked_reuse_result(source, approve=False)
    payload["results"][0]["checkStatus"] = "pending"
    pending = materialize_checked_agent_scientific_facts(
        task_payload=payload,
        result_id="isolated-checked-reuse",
        validated_artifact_content=content,
        occurred_at=NOW,
    )
    assert not any(
        "targetOutputReuseAcceptances" in row["scientificFacts"]
        for row in pending.get("scientificStates", [])
    )
    # Controlled durable review outcome consumed by the unchanged review seam.
    pending["results"][0]["checkStatus"] = "passed"
    artifact = next(
        row
        for row in pending["artifacts"]
        if row["artifactId"] == pending["results"][0]["artifactRefs"][0]
    )
    artifact["artifactState"] = "usable"
    accepted = _materialize_checked_result_projection(
        pending,
        result_id="isolated-checked-reuse",
        occurred_at=NOW,
    )
    task = ResearchTaskV12.model_validate(accepted)
    _task, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    assert expected_project_completion_evaluation(design, state)["canComplete"] is True


def test_explicit_usable_reuse_acceptance_does_not_grant_substage_professional_approval(source):
    from app.models.research_orchestration import ResearchTaskV12
    from app.modules.target_output_reuse_materialization import (
        materialize_checked_agent_scientific_facts,
    )

    payload, design, content = checked_reuse_result(source)
    payload["agentInteractionRecords"] = []
    accepted = materialize_checked_agent_scientific_facts(
        task_payload=payload,
        result_id="isolated-checked-reuse",
        validated_artifact_content=content,
        occurred_at=NOW,
    )
    task = ResearchTaskV12.model_validate(accepted)
    assert [row.interaction_type for row in task.agent_interaction_records] == [
        "target_output_reuse_accepted"
    ]
    _task, state = record_scientific_state_from_durable_facts(task, design, occurred_at=NOW)
    assert expected_project_completion_evaluation(design, state)["canComplete"] is True
    again = materialize_checked_agent_scientific_facts(
        task_payload=accepted,
        result_id="isolated-checked-reuse",
        validated_artifact_content=content,
        occurred_at=NOW,
    )
    assert again["agentInteractionRecords"] == accepted["agentInteractionRecords"]


def test_reuse_projection_isolates_bad_judgment_and_preserves_valid_acceptance(source):
    import json

    from app.modules.target_output_reuse_materialization import (
        materialize_checked_agent_scientific_facts,
    )
    payload, _design, content = checked_reuse_result(source)
    document = json.loads(content)
    bad = dict(document["targetOutputReuseJudgments"][0])
    bad.pop("verdict")
    document["targetOutputReuseJudgments"].insert(0, bad)
    result = materialize_checked_agent_scientific_facts(
        task_payload=payload, result_id="isolated-checked-reuse",
        validated_artifact_content=json.dumps(document), occurred_at=NOW,
    )
    checked = next(row for row in result["results"] if row["resultId"] == "isolated-checked-reuse")
    assert len(checked["checkResult"]["targetOutputReuseJudgments"]) == 1
    assert "target_output_reuse_judgments:invalid_row:0" in checked["checkResult"]["warnings"]
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert len(facts["targetOutputReuseAcceptances"]) == 1


def test_reuse_rationale_gap_preserves_explicit_scoped_acceptance(source):
    import json

    from app.modules.target_output_reuse_materialization import (
        materialize_checked_agent_scientific_facts,
    )
    payload, _design, content = checked_reuse_result(source)
    document = json.loads(content)
    document["targetOutputReuseJudgments"][0].pop("rationale")
    document["targetOutputReuseJudgments"][0]["extra_scientific_note"] = "keep in raw Artifact"
    result = materialize_checked_agent_scientific_facts(
        task_payload=payload, result_id="isolated-checked-reuse",
        validated_artifact_content=json.dumps(document), occurred_at=NOW,
    )
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert len(facts["targetOutputReuseAcceptances"]) == 1

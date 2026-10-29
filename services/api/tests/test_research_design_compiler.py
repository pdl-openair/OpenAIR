from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.models.research_planner_semantic import PlannerSemanticProposalV2
from app.modules.research_design_compiler import (
    ResearchDesignCompilerError,
    compile_research_design_proposal,
)
from app.modules.research_design_service import validate_research_design_proposal


def _catalog() -> list[dict]:
    return [
        {
            "definitionId": "sdr:problem_framing",
            "definitionVersion": 1,
            "stage": "problem_framing",
            "label": "Problem framing",
            "description": "Clarify the research question.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["user:request"],
            "artifactTypes": ["problem_statement"],
            "criterionRefs": ["problem_statement_validator"],
        },
        {
            "definitionId": "sdr:evidence_synthesis",
            "definitionVersion": 1,
            "stage": "solution_design",
            "label": "Evidence synthesis",
            "description": "Synthesize bounded evidence.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["material:one"],
            "artifactTypes": ["evidence_summary", "evidence_table"],
            "criterionRefs": ["evidence_summary_validator", "secondary_validator"],
        },
        {
            "definitionId": "sdr:execution_check",
            "definitionVersion": 1,
            "stage": "execution_validation",
            "label": "Execution check",
            "description": "Validate a bounded execution result.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["artifact:design"],
            "artifactTypes": ["validation_report"],
            "criterionRefs": ["validation_report_validator"],
        },
        {
            "definitionId": "sdr:result_summary",
            "definitionVersion": 1,
            "stage": "result_summary",
            "label": "Result summary",
            "description": "Summarize accepted outputs.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": ["artifact:validated"],
            "artifactTypes": ["result_summary"],
            "criterionRefs": ["result_summary_validator"],
        },
    ]


def _payload() -> dict:
    output = "A bounded battery evidence summary"
    return {
        "schemaVersion": "planner-semantic.v2",
        "goal": "Assess the supplied battery evidence without expanding scope.",
        "understanding": {
            "userIntent": "Assess the supplied battery evidence.",
            "materialSummary": "One bounded source set is available.",
            "primaryResearchQuestions": ["What does the supplied battery evidence support within scope?"],
            "auxiliaryResearchDimensions": ["Source comparability"],
            "targetOutputs": [output],
            "evidenceBoundaries": ["Use only the supplied source set."],
            "missingInputs": [],
            "riskNotes": [],
        },
        "constraints": ["Do not claim experimental validation."],
        "requestedExternalActions": [],
        "workItems": [
            {
                "localKey": "synthesize",
                "objective": "Synthesize the bounded battery evidence.",
                "definitionId": "sdr:evidence_synthesis",
                "inputRefs": ["material:one"],
                "expectedArtifactTypes": ["evidence_summary"],
                "targetOutputRefs": [output],
                "dependsOnLocalKeys": [],
            }
        ],
    }


def _compile(
    payload: dict,
    catalog: list[dict] | None = None,
    *,
    raw_input: str = "Assess the supplied battery evidence.",
    workflow_planning_context: dict | None = None,
) -> dict:
    semantic = PlannerSemanticProposalV2.model_validate(payload)
    return compile_research_design_proposal(
        semantic,
        definition_catalog=catalog or _catalog(),
        raw_input=raw_input,
        workflow_planning_context=workflow_planning_context,
    ).model_dump(mode="json", by_alias=True)


def test_minimal_semantic_proposal_compiles_into_complete_strict_contract() -> None:
    proposal = _compile(_payload())

    instance = proposal["instances"][0]
    assert proposal["planningMode"] == "lightweight"
    assert proposal["planningComplexity"]["expectedStageSpan"] == "one"
    assert instance["acceptanceCriteria"]
    assert proposal["coverageClaimCandidates"]
    assert all(row["criterionKey"] for row in instance["acceptanceCriteria"])
    assert all("dependencyKey" in row for row in instance["dependencies"])
    assert validate_research_design_proposal(
        proposal,
        allowed_definition_ids=frozenset(row["definitionId"] for row in _catalog()),
        definition_stage_by_id={row["definitionId"]: row["stage"] for row in _catalog()},
    ).model_dump(mode="json", by_alias=True) == proposal


def _complete_research_catalog() -> list[dict]:
    return [
        *_catalog(),
        {
            "definitionId": "sdr:hypothesis_update",
            "definitionVersion": 1,
            "stage": "insight_generation",
            "label": "Hypothesis update",
            "description": "Update hypotheses from findings.",
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": [],
            "artifactTypes": ["hypothesis_update"],
            "criterionRefs": ["hypothesis_update_validator"],
        },
        {
            "definitionId": "sdr:report_generation",
            "definitionVersion": 1,
            "stage": "insight_generation",
            "label": "Report generation",
            "description": "Assemble the canonical final research result.",
            "instancePolicy": {"mode": "singleton_collection"},
            "requiredInputRefs": [],
            "artifactTypes": ["canonical_final_result"],
            "criterionRefs": ["final_result_validator"],
        },
    ]


def _hypothesis_payload(*, complete: bool) -> dict:
    payload = _payload()
    payload["goal"] = "Complete the research." if complete else "Return only the intermediate hypothesis update."
    payload["workItems"] = [{
        "localKey": "update_hypothesis",
        "objective": payload["goal"],
        "definitionId": "sdr:hypothesis_update",
        "inputRefs": [],
        "expectedArtifactTypes": ["hypothesis_update"],
        "targetOutputRefs": [payload["understanding"]["targetOutputs"][0]],
        "dependsOnLocalKeys": [],
    }]
    return payload


def test_partial_research_does_not_require_final_producer() -> None:
    proposal = _compile(
        _hypothesis_payload(complete=False),
        _complete_research_catalog(),
        raw_input="Return only the intermediate hypothesis update.",
    )
    assert [row["definitionId"] for row in proposal["instances"]] == ["sdr:hypothesis_update"]


def test_compiler_does_not_infer_final_producer_from_request_wording() -> None:
    proposal = _compile(
        _hypothesis_payload(complete=True),
        _complete_research_catalog(),
        raw_input="Complete the research.",
    )

    assert [row["definitionId"] for row in proposal["instances"]] == ["sdr:hypothesis_update"]
    assert "sdr:report_generation" not in {row["definitionId"] for row in proposal["instances"]}


def test_complete_research_compiles_only_when_planner_selects_final_producer() -> None:
    payload = _hypothesis_payload(complete=True)
    target = payload["understanding"]["targetOutputs"][0]
    payload["workItems"][0]["targetOutputRefs"] = []
    payload["workItems"].append({
        "localKey": "final_report",
        "objective": "Assemble the final research result.",
        "definitionId": "sdr:report_generation",
        "inputRefs": [],
        "expectedArtifactTypes": ["canonical_final_result"],
        "targetOutputRefs": [target],
        "dependsOnLocalKeys": ["update_hypothesis"],
    })

    proposal = _compile(
        payload,
        _complete_research_catalog(),
        raw_input="Complete the research.",
    )
    report = next(row for row in proposal["instances"] if row["definitionId"] == "sdr:report_generation")
    assert report["expectedArtifactTypes"] == ["canonical_final_result"]
    assert len(report["dependencies"]) == 1




def test_same_semantics_and_catalog_compile_to_equivalent_contract() -> None:
    first = _compile(_payload())
    second = _compile(deepcopy(_payload()))

    assert second == first


def test_definition_artifact_and_evaluator_contract_are_authoritative() -> None:
    proposal = _compile(_payload())
    instance = proposal["instances"][0]
    target_criteria = [
        row for row in instance["acceptanceCriteria"] if row["criterionKind"] == "target_output"
    ]

    assert instance["expectedArtifactTypes"] == ["evidence_summary"]
    assert {row["requiredArtifactType"] for row in target_criteria} == {"evidence_summary"}
    assert {row["evaluatorRef"] for row in target_criteria} == {
        "evidence_summary_validator",
        "secondary_validator",
    }
    assert {row["evaluatorType"] for row in target_criteria} == {"agent"}


def test_mixed_target_requirements_compile_with_deterministic_and_professional_coverage() -> None:
    catalog = deepcopy(_catalog())
    catalog[1]["criterionRefs"] = ["comparison_validator"]
    payload = _payload()
    candidate = "Generate candidate structures"
    comparison = "Compare the candidate against the reference"
    payload["understanding"]["targetOutputs"] = [candidate, comparison]
    payload["workItems"][0]["targetOutputRefs"] = [candidate, comparison]
    payload["targetRequirements"] = [{
        "workItemLocalKey": "synthesize", "targetOutputRef": candidate,
        "completionMode": "artifact_presence",
    }, {
        "workItemLocalKey": "synthesize",
        "targetOutputRef": comparison,
        "completionMode": "professional_judgment",
    }]

    proposal = _compile(payload, catalog)
    criteria = {
        row["coverageSourcePaths"][0]: row
        for row in proposal["instances"][0]["acceptanceCriteria"]
        if row["coverageSourcePaths"]
    }

    assert {
        "criterionKind": "target_output",
        "judgmentRequirement": "deterministic",
        "evaluatorType": "deterministic",
        "evaluatorRef": None,
    } == {
        key: criteria["understanding.targetOutputs[0]"][key]
        for key in ("criterionKind", "judgmentRequirement", "evaluatorType", "evaluatorRef")
    }
    assert {
        "criterionKind": "target_output",
        "judgmentRequirement": "domain_professional",
        "evaluatorType": "agent",
        "evaluatorRef": "comparison_validator",
    } == {
        key: criteria["understanding.targetOutputs[1]"][key]
        for key in ("criterionKind", "judgmentRequirement", "evaluatorType", "evaluatorRef")
    }
    quality_criteria = [
        row for row in proposal["instances"][0]["acceptanceCriteria"]
        if row["criterionKind"] == "artifact_quality"
        and row["judgmentRequirement"] == "domain_professional"
    ]
    assert len(quality_criteria) == 1
    assert quality_criteria[0]["requiredArtifactType"] == "evidence_summary"
    assert quality_criteria[0]["evaluatorRef"] == "comparison_validator"


def test_artifact_presence_requirement_preserves_definition_professional_quality_gate() -> None:
    payload = _payload()
    output = payload["understanding"]["targetOutputs"][0]
    payload["targetRequirements"] = [{
        "workItemLocalKey": "synthesize", "targetOutputRef": output,
        "completionMode": "artifact_presence",
    }]

    proposal = _compile(payload, [
        {
            **_catalog()[0],
            "definitionId": "sdr:evidence_synthesis",
            "requiredInputRefs": ["material:one"],
            "artifactTypes": ["evidence_summary"],
            "criterionRefs": ["evidence_validator"],
        },
    ])
    criteria = proposal["instances"][0]["acceptanceCriteria"]

    target = next(row for row in criteria if row["criterionKind"] == "target_output")
    assert {
        "judgmentRequirement": target["judgmentRequirement"],
        "evaluatorType": target["evaluatorType"],
        "evaluatorRef": target["evaluatorRef"],
    } == {
        "judgmentRequirement": "deterministic",
        "evaluatorType": "deterministic",
        "evaluatorRef": None,
    }
    quality = [row for row in criteria if row["criterionKind"] == "artifact_quality"]
    assert len(quality) == 1
    assert {
        "judgmentRequirement": quality[0]["judgmentRequirement"],
        "evaluatorType": quality[0]["evaluatorType"],
        "evaluatorRef": quality[0]["evaluatorRef"],
    } == {
        "judgmentRequirement": "domain_professional",
        "evaluatorType": "agent",
        "evaluatorRef": "evidence_validator",
    }
    assert proposal["instances"][0]["expectedArtifactTypes"] == ["evidence_summary"]


def test_professional_target_requirement_uses_frozen_evaluator_and_preserves_definition_quality() -> None:
    payload = _payload()
    output = payload["understanding"]["targetOutputs"][0]
    catalog = deepcopy(_catalog())
    catalog[1]["criterionRefs"] = ["comparison_validator"]
    payload["targetRequirements"] = [{
        "workItemLocalKey": "synthesize", "targetOutputRef": output,
        "completionMode": "professional_judgment",
    }]

    proposal = _compile(payload, catalog)
    criteria = proposal["instances"][0]["acceptanceCriteria"]
    target = next(row for row in criteria if row["criterionKind"] == "target_output")
    quality = next(row for row in criteria if row["criterionKind"] == "artifact_quality")
    for criterion in (target, quality):
        assert criterion["judgmentRequirement"] == "domain_professional"
        assert criterion["evaluatorType"] == "agent"
        assert criterion["evaluatorRef"] == "comparison_validator"
    assert target["coverageSourcePaths"] == ["understanding.targetOutputs[0]"]
    assert quality["coverageSourcePaths"] == []


def test_professional_target_requirement_without_definition_evaluator_fails_closed() -> None:
    catalog = deepcopy(_catalog())
    catalog[1]["criterionRefs"] = []
    payload = _payload()
    output = payload["understanding"]["targetOutputs"][0]
    payload["targetRequirements"] = [{
        "workItemLocalKey": "synthesize", "targetOutputRef": output,
        "completionMode": "professional_judgment",
    }]

    with pytest.raises(ResearchDesignCompilerError) as captured:
        _compile(payload, catalog)

    assert captured.value.safe_error_code == "planner_semantic_professional_evaluator_unavailable"
    assert captured.value.safe_field_paths == ("/targetRequirements/0/completionMode",)
    repair = captured.value.safe_repair_context
    assert repair["repairKind"] == "focused_semantic"
    assert repair["schemaVersion"] == "planner-semantic-repair.v2"
    assert repair["fieldPaths"] == ["/targetRequirements/0/completionMode"]
    assert repair["semanticProposal"]["targetRequirements"][0]["completionMode"] == "professional_judgment"
    definition_id = catalog[1]["definitionId"]
    assert repair["failedTargetRequirement"]["definitionId"] == definition_id
    assert repair["definitionContractFacts"][definition_id]["criterionRefs"] == []


def test_professional_target_requirement_with_multiple_definition_evaluators_fails_closed() -> None:
    payload = _payload()
    output = payload["understanding"]["targetOutputs"][0]
    payload["targetRequirements"] = [{
        "workItemLocalKey": "synthesize", "targetOutputRef": output,
        "completionMode": "professional_judgment",
    }]

    with pytest.raises(ResearchDesignCompilerError) as captured:
        _compile(payload)

    assert captured.value.safe_error_code == "planner_semantic_professional_evaluator_ambiguous"
    assert captured.value.safe_field_paths == ("/targetRequirements/0/completionMode",)


@pytest.mark.parametrize("injection", [
    {"evaluatorRef": "invented_evaluator"},
    {"agentId": "invented_agent"},
])
def test_target_requirements_cannot_carry_evaluator_or_agent_authority(injection: dict) -> None:
    payload = _payload()
    output = payload["understanding"]["targetOutputs"][0]
    payload["targetRequirements"] = [{
        "workItemLocalKey": "synthesize", "targetOutputRef": output,
        "completionMode": "professional_judgment", **injection,
    }]

    with pytest.raises(ValueError):
        PlannerSemanticProposalV2.model_validate(payload)








def test_target_requirement_leaves_workflow_topology_unchanged() -> None:
    payload = _payload()
    catalog = deepcopy(_catalog())
    catalog[1]["criterionRefs"] = ["comparison_validator"]
    baseline = _compile(payload, catalog)
    output = payload["understanding"]["targetOutputs"][0]
    payload["targetRequirements"] = [{
        "workItemLocalKey": "synthesize", "targetOutputRef": output,
        "completionMode": "professional_judgment",
    }]
    with_requirement = _compile(payload, catalog)

    def topology(proposal: dict) -> list[dict]:
        return [{
            "instanceKey": row["instanceKey"],
            "definitionId": row["definitionId"],
            "dependencies": row["dependencies"],
            "sequenceHint": row["sequenceHint"],
        } for row in proposal["instances"]]

    assert topology(with_requirement) == topology(baseline)


def test_legacy_semantics_without_target_requirements_remain_unchanged() -> None:
    payload = _payload()
    legacy = _compile(payload)
    payload["targetRequirements"] = []

    assert _compile(payload) == legacy
    criteria = [
        criterion for criterion in legacy["instances"][0]["acceptanceCriteria"]
        if criterion["criterionKind"] in {"target_output", "artifact_quality"}
    ]
    assert [{
        "criterionKind": criterion["criterionKind"],
        "targetRef": criterion["coverageSourcePaths"][0] if criterion["coverageSourcePaths"] else None,
        "artifactType": criterion["requiredArtifactType"],
        "judgmentRequirement": criterion["judgmentRequirement"],
        "evaluatorRef": criterion["evaluatorRef"],
    } for criterion in criteria] == [
        {
            "criterionKind": "target_output",
            "targetRef": "understanding.targetOutputs[0]",
            "artifactType": "evidence_summary",
            "judgmentRequirement": "domain_professional",
            "evaluatorRef": "evidence_summary_validator",
        },
        {
            "criterionKind": "target_output",
            "targetRef": "understanding.targetOutputs[0]",
            "artifactType": "evidence_summary",
            "judgmentRequirement": "domain_professional",
            "evaluatorRef": "secondary_validator",
        },
        {
            "criterionKind": "artifact_quality",
            "targetRef": None,
            "artifactType": "evidence_summary",
            "judgmentRequirement": "domain_professional",
            "evaluatorRef": "evidence_summary_validator",
        },
        {
            "criterionKind": "artifact_quality",
            "targetRef": None,
            "artifactType": "evidence_summary",
            "judgmentRequirement": "domain_professional",
            "evaluatorRef": "secondary_validator",
        },
    ]


def test_semantic_only_definition_compiles_without_fabricating_canonical_contract() -> None:
    catalog = [{
        "definitionId": "ope:experiment_design",
        "definitionVersion": 1,
        "stage": "solution_design",
        "label": "Experiment design",
        "description": "Design a reproducible experiment for the requested comparison.",
        "instancePolicy": {"mode": "repeatable"},
        "contractComplete": False,
        "requiredInputRefs": [],
        "artifactTypes": [],
        "criterionRefs": [],
    }]
    payload = _payload()
    output = "A reproducible experiment design"
    payload["understanding"]["targetOutputs"] = [output]
    payload["workItems"] = [{
        "localKey": "experiment",
        "objective": "Design the reproducible comparison experiment.",
        "definitionId": "ope:experiment_design",
        "inputRefs": ["user_goal"],
        "expectedArtifactTypes": [],
        "targetOutputRefs": [output],
        "dependsOnLocalKeys": [],
    }]

    proposal = _compile(payload, catalog)
    instance = proposal["instances"][0]
    target_criteria = [
        row for row in instance["acceptanceCriteria"] if row["criterionKind"] == "target_output"
    ]

    assert instance["expectedArtifactTypes"] == ["ope.experiment_design.planned_output"]
    assert len(target_criteria) == 1
    assert target_criteria[0]["requiredArtifactType"] == "ope.experiment_design.planned_output"
    assert target_criteria[0]["evaluatorType"] == "deterministic"
    assert target_criteria[0]["evaluatorRef"] is None


def test_semantic_only_definition_ignores_provider_invented_artifact_mechanics() -> None:
    catalog = [{
        "definitionId": "ope:experiment_design",
        "definitionVersion": 1,
        "stage": "solution_design",
        "label": "Experiment design",
        "description": "Design a reproducible experiment for the requested comparison.",
        "instancePolicy": {"mode": "repeatable"},
        "contractComplete": False,
        "requiredInputRefs": [],
        "artifactTypes": [],
        "criterionRefs": [],
    }]
    payload = _payload()
    payload["understanding"]["targetOutputs"] = ["A reproducible experiment design"]
    payload["workItems"][0].update({
        "definitionId": "ope:experiment_design",
        "expectedArtifactTypes": ["invented_provider_artifact"],
        "targetOutputRefs": ["A reproducible experiment design"],
    })

    proposal = _compile(payload, catalog)

    assert proposal["instances"][0]["expectedArtifactTypes"] == [
        "ope.experiment_design.planned_output"
    ]
    assert "invented_provider_artifact" not in json.dumps(proposal, ensure_ascii=False)


def test_unknown_definition_fails_closed_and_never_selects_catalog_first_item() -> None:
    payload = _payload()
    payload["workItems"][0]["definitionId"] = "unknown:definition"
    semantic = PlannerSemanticProposalV2.model_validate(payload)

    with pytest.raises(ResearchDesignCompilerError) as captured:
        compile_research_design_proposal(
            semantic,
            definition_catalog=_catalog(),
            raw_input="Assess the supplied battery evidence.",
        )

    assert captured.value.safe_error_code == "research_design_definition_unknown"
    assert captured.value.safe_field_paths == ("/workItems/0/definitionId",)
    assert captured.value.safe_repair_context["definitionCandidates"][0] == "sdr:problem_framing"


def test_topic_only_semantics_compile_one_real_node_not_four_stage_template() -> None:
    payload = _payload()
    payload["goal"] = "Clarify a topic-only research direction."
    payload["understanding"].update({
        "userIntent": "Clarify a topic-only research direction.",
        "materialSummary": None,
        "primaryResearchQuestions": ["What bounded research question should this topic become?"],
        "auxiliaryResearchDimensions": [],
        "targetOutputs": ["A bounded research question"],
        "evidenceBoundaries": ["No paper packet has been supplied."],
    })
    payload["workItems"] = [{
        "localKey": "frame",
        "objective": "Frame the research question without claiming evidence findings.",
        "definitionId": "sdr:problem_framing",
        "inputRefs": ["user:request"],
        "expectedArtifactTypes": [],
        "targetOutputRefs": ["A bounded research question"],
        "dependsOnLocalKeys": [],
    }]

    proposal = _compile(payload)

    assert len(proposal["instances"]) == 1
    assert proposal["instances"][0]["definitionId"] == "sdr:problem_framing"
    assert proposal["scopeBoundary"]["maxSubstageInstances"] == 1


def test_user_constraints_outputs_and_evidence_boundaries_are_preserved_and_covered() -> None:
    proposal = _compile(_payload())
    source_paths = {row["sourcePath"] for row in proposal["coverageClaimCandidates"]}

    assert proposal["goal"] == _payload()["goal"]
    assert proposal["constraints"] == _payload()["constraints"]
    assert proposal["understanding"]["primaryResearchQuestions"] == _payload()["understanding"]["primaryResearchQuestions"]
    assert proposal["understanding"]["auxiliaryResearchDimensions"] == _payload()["understanding"]["auxiliaryResearchDimensions"]
    assert proposal["understanding"]["targetOutputs"] == _payload()["understanding"]["targetOutputs"]
    assert proposal["understanding"]["evidenceBoundaries"] == _payload()["understanding"]["evidenceBoundaries"]
    assert "constraints[0]" in source_paths
    assert "understanding.targetOutputs[0]" in source_paths
    assert "understanding.evidenceBoundaries[0]" in source_paths
    serialized = json.dumps(proposal, ensure_ascii=False)
    assert "battery chemistry is superior" not in serialized
    assert "experiment succeeded" not in serialized


def test_dependency_keys_and_topological_sequence_are_server_generated_and_stable() -> None:
    payload = _payload()
    output = payload["understanding"]["targetOutputs"][0]
    payload["workItems"] = [
        {
            "localKey": "frame",
            "objective": "Frame the question.",
            "definitionId": "sdr:problem_framing",
            "inputRefs": ["user:request"],
            "expectedArtifactTypes": ["problem_statement"],
            "targetOutputRefs": [],
            "dependsOnLocalKeys": [],
        },
        {
            "localKey": "synthesize",
            "objective": "Synthesize the bounded evidence.",
            "definitionId": "sdr:evidence_synthesis",
            "inputRefs": ["material:one"],
            "expectedArtifactTypes": ["evidence_summary"],
            "targetOutputRefs": [output],
            "dependsOnLocalKeys": ["frame"],
        },
    ]

    proposal = _compile(payload)
    by_key = {row["instanceKey"]: row for row in proposal["instances"]}

    assert by_key["frame"]["sequenceHint"] == 0
    assert by_key["synthesize"]["sequenceHint"] == 1
    dependency = by_key["synthesize"]["dependencies"][0]
    assert dependency["upstreamInstanceKey"] == "frame"
    assert dependency["conditionType"] == "instance_completed"
    assert dependency["dependencyKey"] == _compile(deepcopy(payload))["instances"][1]["dependencies"][0]["dependencyKey"]


def test_planning_depth_is_computed_from_final_graph() -> None:
    assert _compile(_payload())["planningMode"] == "lightweight"

    partial = _payload()
    output = partial["understanding"]["targetOutputs"][0]
    partial["workItems"] = [
        {
            "localKey": "frame",
            "objective": "Frame the question.",
            "definitionId": "sdr:problem_framing",
            "inputRefs": ["user:request"],
            "expectedArtifactTypes": [],
            "targetOutputRefs": [],
            "dependsOnLocalKeys": [],
        },
        {
            "localKey": "synthesize",
            "objective": "Synthesize evidence.",
            "definitionId": "sdr:evidence_synthesis",
            "inputRefs": ["material:one"],
            "expectedArtifactTypes": [],
            "targetOutputRefs": [output],
            "dependsOnLocalKeys": ["frame"],
        },
    ]
    assert _compile(partial)["planningMode"] == "partial"

    sdr = deepcopy(partial)
    sdr["workItems"].extend([
        {
            "localKey": "validate",
            "objective": "Validate the bounded result.",
            "definitionId": "sdr:execution_check",
            "inputRefs": ["artifact:design"],
            "expectedArtifactTypes": [],
            "targetOutputRefs": [],
            "dependsOnLocalKeys": ["synthesize"],
        },
        {
            "localKey": "summarize",
            "objective": "Summarize accepted results.",
            "definitionId": "sdr:result_summary",
            "inputRefs": ["artifact:validated"],
            "expectedArtifactTypes": [],
            "targetOutputRefs": [],
            "dependsOnLocalKeys": ["validate"],
        },
    ])
    assert _compile(sdr)["planningMode"] == "sdr"


def test_semantic_artifact_conflict_is_focused_and_does_not_mutate_other_fields() -> None:
    payload = _payload()
    payload["workItems"][0]["expectedArtifactTypes"] = ["invented_artifact"]
    semantic = PlannerSemanticProposalV2.model_validate(payload)

    with pytest.raises(ResearchDesignCompilerError) as captured:
        compile_research_design_proposal(
            semantic,
            definition_catalog=_catalog(),
            raw_input="Assess the supplied battery evidence.",
        )

    assert captured.value.safe_error_code == "planner_semantic_artifact_contract_conflict"
    assert captured.value.safe_field_paths == ("/workItems/0/expectedArtifactTypes",)
    context = captured.value.safe_repair_context
    assert context["semanticProposal"]["goal"] == payload["goal"]
    assert context["fieldPaths"] == ["/workItems/0/expectedArtifactTypes"]
    assert context["artifactCandidatesByDefinition"] == {
        "sdr:evidence_synthesis": ["evidence_summary", "evidence_table"]
    }


def test_final_validator_rejects_missing_mechanical_sections_instead_of_soft_filling() -> None:
    from app.modules.research_design_service import ResearchDesignProposalError

    proposal = _compile(_payload())
    proposal.pop("scopeBoundary")

    with pytest.raises(ResearchDesignProposalError) as captured:
        validate_research_design_proposal(
            proposal,
            allowed_definition_ids=frozenset(row["definitionId"] for row in _catalog()),
            definition_stage_by_id={row["definitionId"]: row["stage"] for row in _catalog()},
        )

    assert captured.value.safe_error_code == "research_design_proposal_invalid"
    assert "/scopeBoundary" in captured.value.safe_field_paths


def test_compiler_layers_shared_guards_once_and_avoids_output_artifact_evaluator_cross_product() -> None:
    payload = _payload()
    payload["understanding"]["targetOutputs"] = ["output one", "output two"]
    payload["understanding"]["evidenceBoundaries"] = ["Use supplied evidence only.", "Label uncertainty."]
    payload["constraints"] = ["No experimental claims.", "Keep source attribution."]
    payload["requestedExternalActions"] = ["external_search"]
    payload["workItems"] = [
        {
            "localKey": "frame",
            "objective": "Frame the bounded question.",
            "definitionId": "sdr:problem_framing",
            "inputRefs": ["user:request"],
            "expectedArtifactTypes": ["problem_statement"],
            "targetOutputRefs": [],
            "dependsOnLocalKeys": [],
        },
        {
            "localKey": "synthesize",
            "objective": "Synthesize both bounded outputs.",
            "definitionId": "sdr:evidence_synthesis",
            "inputRefs": ["material:one"],
            "expectedArtifactTypes": [],
            "targetOutputRefs": ["output one", "output two"],
            "dependsOnLocalKeys": ["frame"],
        },
    ]

    proposal = _compile(payload)
    by_key = {row["instanceKey"]: row for row in proposal["instances"]}
    synthesis = by_key["synthesize"]
    target_criteria = [
        row for row in synthesis["acceptanceCriteria"] if row["criterionKind"] == "target_output"
    ]
    quality_criteria = [
        row for row in synthesis["acceptanceCriteria"] if row["criterionKind"] == "artifact_quality"
    ]

    # Two outputs × two evaluators, not outputs × artifacts × evaluators.
    assert len(target_criteria) == 4
    assert {row["evaluatorRef"] for row in target_criteria} == {
        "evidence_summary_validator",
        "secondary_validator",
    }
    assert {row["requiredArtifactType"] for row in target_criteria} == {"evidence_summary"}
    assert {
        (row["requiredArtifactType"], row["evaluatorRef"])
        for row in quality_criteria
    } == {
        ("evidence_summary", "evidence_summary_validator"),
        ("evidence_summary", "secondary_validator"),
        ("evidence_table", "evidence_summary_validator"),
        ("evidence_table", "secondary_validator"),
    }

    shared_kinds = {
        "constraint_compliance",
        "evidence_boundary_compliance",
        "external_action_guard",
        "risk_guard",
    }
    shared = [
        row
        for instance in proposal["instances"]
        for row in instance["acceptanceCriteria"]
        if row["criterionKind"] in shared_kinds
    ]
    # 2 constraints + 2 evidence boundaries + 1 external guard + 2 prohibited
    # action guards + 1 human-gate guard. Each appears once design-wide.
    assert len(shared) == 8
    assert not [
        row for row in synthesis["acceptanceCriteria"] if row["criterionKind"] in shared_kinds
    ]


def test_compiler_emits_real_conservative_operational_budget_boundary() -> None:
    proposal = _compile(_payload())
    budget = proposal["budgetBoundary"]

    assert budget["maxModelCalls"] >= 8
    assert budget["maxToolCalls"] >= 8
    assert budget["maxWallTimeSec"] >= 1800
    assert budget["maxCostAmount"] is None
    assert budget["currency"] is None


def test_compiler_enforces_acceptance_criterion_and_serialized_size_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules import research_design_compiler as compiler_module

    monkeypatch.setattr(compiler_module, "MAX_COMPILED_ACCEPTANCE_CRITERIA", 1)
    with pytest.raises(ResearchDesignCompilerError) as criterion_error:
        _compile(_payload())
    assert criterion_error.value.safe_error_code == "research_design_compiler_scale_limit"
    assert criterion_error.value.safe_field_paths == ("/workItems",)

    monkeypatch.setattr(compiler_module, "MAX_COMPILED_ACCEPTANCE_CRITERIA", 10_000)
    monkeypatch.setattr(compiler_module, "MAX_COMPILED_PROPOSAL_BYTES", 1)
    with pytest.raises(ResearchDesignCompilerError) as size_error:
        _compile(_payload())
    assert size_error.value.safe_error_code == "research_design_compiler_scale_limit"
    assert size_error.value.safe_field_paths == ("/workItems",)


def _adaptive_workflow_context() -> dict:
    return {
        "schemaVersion": "workflow-planning-context.v2",
        "workflowId": "adaptive_workflow",
        "workflowVersion": 2,
        "workflowContentHash": "sha256:" + "c" * 64,
        "skillPack": "test",
        "description": "Responsibility-based adaptive workflow.",
        "planningPolicy": {
            "responsibilityPlanning": "project_adaptive",
            "existingResultReuse": "validated_existing_results",
            "conditionalResponsibilities": "planner_selected",
        },
        "coreResponsibilities": [
            {
                "nodeKey": "analysis",
                "definitionId": "sdr:hypothesis_update",
                "definitionVersion": 1,
                "displayOrder": 10,
            },
            {
                "nodeKey": "conclusion",
                "definitionId": "sdr:report_generation",
                "definitionVersion": 1,
                "displayOrder": 20,
            },
        ],
        "conditionalResponsibilities": [
            {
                "nodeKey": "diagnosis",
                "definitionId": "sdr:execution_check",
                "definitionVersion": 1,
                "displayOrder": 30,
            }
        ],
        "dependencies": [
            {
                "upstreamNodeKey": "analysis",
                "downstreamNodeKey": "conclusion",
                "upstreamDefinitionId": "sdr:hypothesis_update",
                "downstreamDefinitionId": "sdr:report_generation",
                "dependencyType": "scientific_dependency",
            },
            {
                "upstreamNodeKey": "analysis",
                "downstreamNodeKey": "diagnosis",
                "upstreamDefinitionId": "sdr:hypothesis_update",
                "downstreamDefinitionId": "sdr:execution_check",
                "dependencyType": "scientific_dependency",
            },
            {
                "upstreamNodeKey": "diagnosis",
                "downstreamNodeKey": "conclusion",
                "upstreamDefinitionId": "sdr:execution_check",
                "downstreamDefinitionId": "sdr:report_generation",
                "dependencyType": "scientific_dependency",
            },
        ],
        "boundaryPolicy": {
            "allowedExternalActions": [],
            "confirmationRequiredActions": [],
            "prohibitedActions": ["external_write"],
        },
    }


def _adaptive_semantic_payload(*, reuse_ref: str | None = None) -> dict:
    target = "Current-project conclusion"
    if reuse_ref is None:
        analysis_items = [
            {
                "localKey": "analysis_a",
                "objective": "Analyze one independent evidence slice.",
                "definitionId": "sdr:hypothesis_update",
                "inputRefs": [],
                "expectedArtifactTypes": ["hypothesis_update"],
                "targetOutputRefs": [],
                "dependsOnLocalKeys": [],
            },
            {
                "localKey": "analysis_b",
                "objective": "Analyze a second independent evidence slice.",
                "definitionId": "sdr:hypothesis_update",
                "inputRefs": [],
                "expectedArtifactTypes": ["hypothesis_update"],
                "targetOutputRefs": [],
                "dependsOnLocalKeys": [],
            },
        ]
        conclusion_dependencies = ["analysis_a", "analysis_b"]
        conclusion_inputs: list[str] = []
        analysis_coverage = {
            "responsibilityKey": "analysis",
            "definitionId": "sdr:hypothesis_update",
            "requirement": "core",
            "coverageMode": "planned_work",
            "workItemLocalKeys": ["analysis_a", "analysis_b"],
            "reusedResultRefs": [],
            "rationale": "Two independent project tasks are useful for this responsibility.",
        }
    else:
        analysis_items = []
        conclusion_dependencies = []
        conclusion_inputs = [reuse_ref]
        analysis_coverage = {
            "responsibilityKey": "analysis",
            "definitionId": "sdr:hypothesis_update",
            "requirement": "core",
            "coverageMode": "reused_result",
            "workItemLocalKeys": [],
            "reusedResultRefs": [reuse_ref],
            "rationale": "A traceable existing formal result is reused after applicability review.",
        }
    return {
        "schemaVersion": "planner-semantic.v2",
        "goal": "Form a current-project conclusion from the required responsibilities.",
        "understanding": {
            "userIntent": "Answer the current research question.",
            "materialSummary": None,
            "primaryResearchQuestions": ["What does the current evidence support?"],
            "auxiliaryResearchDimensions": [],
            "targetOutputs": [target],
            "evidenceBoundaries": ["Use only traceable current or validated reusable evidence."],
            "missingInputs": [],
            "riskNotes": [],
        },
        "constraints": [],
        "requestedExternalActions": [],
        "workItems": [
            *analysis_items,
            {
                "localKey": "conclusion",
                "objective": "Produce the current-project conclusion.",
                "definitionId": "sdr:report_generation",
                "inputRefs": conclusion_inputs,
                "expectedArtifactTypes": ["canonical_final_result"],
                "targetOutputRefs": [target],
                "dependsOnLocalKeys": conclusion_dependencies,
            },
        ],
        "targetRequirements": [],
        "workflowResponsibilityCoverage": [
            analysis_coverage,
            {
                "responsibilityKey": "conclusion",
                "definitionId": "sdr:report_generation",
                "requirement": "core",
                "coverageMode": "planned_work",
                "workItemLocalKeys": ["conclusion"],
                "reusedResultRefs": [],
                "rationale": "The current project must still answer the user with current evidence.",
            },
            {
                "responsibilityKey": "diagnosis",
                "definitionId": "sdr:execution_check",
                "requirement": "conditional",
                "coverageMode": "not_selected",
                "workItemLocalKeys": [],
                "reusedResultRefs": [],
                "rationale": "No discrepancy or abnormal result currently requires diagnosis.",
            },
        ],
    }


def test_responsibility_workflow_allows_multiple_project_tasks_for_one_core_responsibility() -> None:
    proposal = _compile(
        _adaptive_semantic_payload(),
        _complete_research_catalog(),
        raw_input="Answer the current research question.",
        workflow_planning_context=_adaptive_workflow_context(),
    )

    assert [row["definitionId"] for row in proposal["instances"]].count("sdr:hypothesis_update") == 2
    coverage = {row["responsibilityKey"]: row for row in proposal["workflowResponsibilityCoverage"]}
    assert coverage["analysis"]["coverageMode"] == "planned_work"
    assert len(coverage["analysis"]["instanceKeys"]) == 2
    assert coverage["diagnosis"]["coverageMode"] == "not_selected"


def test_responsibility_workflow_can_reuse_explicit_formal_result_without_claiming_reexecution() -> None:
    reused_ref = "artifact:formal-analysis-123"
    proposal = _compile(
        _adaptive_semantic_payload(reuse_ref=reused_ref),
        _complete_research_catalog(),
        raw_input=f"Use existing formal result {reused_ref} where still applicable and answer the current question.",
        workflow_planning_context=_adaptive_workflow_context(),
    )

    assert [row["definitionId"] for row in proposal["instances"]] == ["sdr:report_generation"]
    coverage = {row["responsibilityKey"]: row for row in proposal["workflowResponsibilityCoverage"]}
    assert coverage["analysis"] == {
        "responsibilityKey": "analysis",
        "definitionId": "sdr:hypothesis_update",
        "requirement": "core",
        "coverageMode": "reused_result",
        "instanceKeys": [],
        "reusedResultRefs": [reused_ref],
        "rationale": "A traceable existing formal result is reused after applicability review.",
    }
    assert reused_ref in proposal["instances"][0]["inputRefs"]

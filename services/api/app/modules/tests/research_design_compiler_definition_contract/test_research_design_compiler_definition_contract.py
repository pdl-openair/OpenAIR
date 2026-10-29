from __future__ import annotations

from app.models.research_planner_semantic import PlannerSemanticProposalV2
from app.modules.research_design_compiler import compile_research_design_proposal


def _semantic_proposal() -> PlannerSemanticProposalV2:
    return PlannerSemanticProposalV2.model_validate({
        "schemaVersion": "planner-semantic.v2",
        "goal": "Assess the supplied evidence without expanding scope.",
        "understanding": {
            "userIntent": "Assess the supplied evidence.",
            "materialSummary": "One bounded source set is available.",
            "primaryResearchQuestions": ["What does the supplied evidence support?"],
            "auxiliaryResearchDimensions": ["Source comparability"],
            "targetOutputs": ["A bounded evidence summary"],
            "evidenceBoundaries": ["Use only the supplied source set."],
            "missingInputs": [],
            "riskNotes": [],
        },
        "constraints": ["Do not claim experimental validation."],
        "requestedExternalActions": [],
        "workItems": [{
            "localKey": "synthesize",
            "objective": "Synthesize the bounded evidence.",
            "definitionId": "demo:definition",
            "inputRefs": ["material:one"],
            "expectedArtifactTypes": ["artifact_a"],
            "targetOutputRefs": ["A bounded evidence summary"],
            "dependsOnLocalKeys": [],
        }],
    })


def test_compiler_uses_explicit_evaluator_refs_instead_of_legacy_criterion_refs() -> None:
    proposal = compile_research_design_proposal(
        _semantic_proposal(),
        definition_catalog=[{
            "definitionId": "demo:definition",
            "definitionVersion": 1,
            "stage": "solution_design",
            "label": "Demo",
            "description": "Demo Definition",
            "requiredInputRefs": ["material:one"],
            "artifactTypes": ["artifact_a"],
            "evaluatorRefs": ["domain_reviewer"],
            "criterionRefs": ["quality_rule_must_not_be_evaluator"],
            "contractComplete": True,
            "instancePolicy": {"mode": "repeatable"},
        }],
        raw_input="Assess the supplied evidence.",
    ).model_dump(mode="json", by_alias=True)

    evaluator_refs = {
        criterion["evaluatorRef"]
        for criterion in proposal["instances"][0]["acceptanceCriteria"]
        if criterion["evaluatorRef"] is not None
    }
    assert evaluator_refs == {"domain_reviewer"}
    assert "quality_rule_must_not_be_evaluator" not in evaluator_refs

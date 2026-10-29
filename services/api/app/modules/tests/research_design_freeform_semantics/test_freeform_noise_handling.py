from __future__ import annotations

from app.modules.research_design_compiler import compile_research_design_proposal
from app.modules.research_design_freeform_semantics import (
    assemble_freeform_semantic_proposal,
    parse_freeform_scientific_brief,
)


def _catalog() -> list[dict]:
    return [
        {
            "definitionId": "demo:frame_problem",
            "definitionVersion": 1,
            "stage": "problem_modeling",
            "label": "Frame the problem",
            "description": "Frame the research problem for this query.",
            "planningApplicability": {
                "scopeKind": "general",
                "scopeLabel": "General research work",
                "scopeDescription": "Use when framing a research problem.",
                "whenToUse": "When the request needs a clear problem frame.",
            },
            "instancePolicy": {"mode": "singleton_collection"},
            "requiredInputRefs": [],
            "artifactTypes": [],
            "criterionRefs": [],
            "contractComplete": False,
        },
        {
            "definitionId": "demo:analyze_evidence",
            "definitionVersion": 1,
            "stage": "insight_generation",
            "label": "Analyze evidence",
            "description": "Analyze the evidence needed for this query.",
            "planningApplicability": {
                "scopeKind": "general",
                "scopeLabel": "General research work",
                "scopeDescription": "Use when evidence needs analysis.",
                "whenToUse": "When the request requires an evidence-based answer.",
            },
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": [],
            "artifactTypes": [],
            "criterionRefs": [],
            "contractComplete": False,
        },
    ]


def test_freeform_assembly_discards_noise_and_compiles_remaining_registered_work() -> None:
    brief = parse_freeform_scientific_brief(
        {
            "researchGoal": "Assess whether answer style biases an LLM judge.",
            "targetOutputs": ["Evidence summary", "Next validation step", "Evidence summary"],
            "workItems": [
                {"workTypeChoice": "not_registered", "scientificObjective": "Ignore this."},
                {
                    "workTypeChoice": "work_type_1",
                    "scientificObjective": "Frame the style-bias question.",
                    "targetOutputNumbers": [1, 1, 99],
                    "prerequisiteWorkItemNumbers": [2],
                },
                {
                    "workTypeChoice": "work_type_1",
                    "scientificObjective": "Duplicate singleton selection.",
                },
                {
                    "workTypeChoice": "work_type_2",
                    "scientificObjective": "Analyze current research evidence.",
                    "targetOutputNumbers": [2],
                    "prerequisiteWorkItemNumbers": [2, 99],
                },
            ],
        }
    )

    semantic = assemble_freeform_semantic_proposal(
        brief,
        definition_catalog=_catalog(),
        raw_input="Does answer style bias an LLM judge?",
    )

    assert [row.definition_id for row in semantic.work_items] == [
        "demo:frame_problem",
        "demo:analyze_evidence",
    ]
    assert semantic.understanding.target_outputs == ["Evidence summary", "Next validation step"]
    assert semantic.work_items[0].depends_on_local_keys == []
    assert semantic.work_items[1].depends_on_local_keys == ["planned_work_01"]

    proposal = compile_research_design_proposal(
        semantic,
        definition_catalog=_catalog(),
        raw_input="Does answer style bias an LLM judge?",
    )
    assert len(proposal.instances) == 2

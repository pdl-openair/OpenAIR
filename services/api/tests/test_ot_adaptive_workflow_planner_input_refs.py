from __future__ import annotations

def test_adaptive_workflow_authorizes_confirmed_research_goal_for_every_work_item() -> None:
    from app.modules.research_design_planner_assembly import build_adaptive_workflow_semantics
    from app.modules.research_design_workflow_semantics import WorkflowProjectSemanticBriefV4

    catalog = [
        {
            "definitionId": "opentrajectory:test_analysis",
            "definitionVersion": 1,
            "description": "Analyze the current research question.",
            "artifactTypes": ["analysis_result"],
        }
    ]
    context = {
        "schemaVersion": "workflow-planning-context.v2",
        "coreResponsibilities": [
            {
                "nodeKey": "analysis",
                "definitionId": "opentrajectory:test_analysis",
                "definitionVersion": 1,
                "displayOrder": 10,
            }
        ],
        "conditionalResponsibilities": [],
        "dependencies": [],
        "boundaryPolicy": {
            "allowedExternalActions": [],
            "prohibitedActions": [],
        },
    }
    brief = WorkflowProjectSemanticBriefV4.model_validate(
        {
            "researchGoal": "Answer the confirmed research question.",
            "primaryResearchQuestions": ["What does the current evidence support?"],
            "auxiliaryResearchDimensions": [],
            "targetOutputs": ["A traceable current conclusion."],
            "constraints": [],
            "scientificUnknowns": [],
            "risksAndTradeoffs": [],
            "responsibilityDeltas": [
                {
                    "responsibilityChoice": "responsibility_1",
                    "workItemScientificContexts": [
                        "Analyze the evidence needed for the current conclusion.",
                        "Separate the material applicability check from the conclusion.",
                    ],
                }
            ],
        }
    )

    semantic = build_adaptive_workflow_semantics(
        raw_input="Answer the confirmed research question.",
        definition_catalog=catalog,
        workflow_planning_context=context,
        brief=brief,
    )

    assert [row.input_refs for row in semantic.work_items] == [
        ["research_goal"],
        ["research_goal"],
    ]


def test_terminal_core_responsibility_is_restored_when_model_suggests_reuse() -> None:
    from app.modules.research_design_planner_assembly import build_adaptive_workflow_semantics
    from app.modules.research_design_workflow_semantics import WorkflowProjectSemanticBriefV4

    catalog = [{
        "definitionId": "opentrajectory:current_answer",
        "definitionVersion": 1,
        "description": "Produce the current project's answer.",
        "artifactTypes": ["research_result"],
    }]
    context = {
        "schemaVersion": "workflow-planning-context.v2",
        "coreResponsibilities": [{
            "nodeKey": "current_answer",
            "definitionId": "opentrajectory:current_answer",
            "definitionVersion": 1,
            "displayOrder": 10,
        }],
        "conditionalResponsibilities": [],
        "dependencies": [],
        "boundaryPolicy": {
            "allowedExternalActions": [],
            "prohibitedActions": [],
        },
    }
    brief = WorkflowProjectSemanticBriefV4.model_validate({
        "researchGoal": "Produce a current answer.",
        "primaryResearchQuestions": ["What does the current evidence support?"],
        "auxiliaryResearchDimensions": [],
        "targetOutputs": ["A current conclusion."],
        "constraints": [],
        "scientificUnknowns": [],
        "risksAndTradeoffs": [],
        "responsibilityDeltas": [{
            "responsibilityChoice": "responsibility_1",
            "reuseCandidateKey": "planning_source_1",
        }],
        "planningSourceUses": [{
            "sourceChoice": "planning_source_1",
            "adoptedUnitNumbers": [1],
            "notAdoptedUnitNumbers": [],
            "revalidationUnitNumbers": [],
        }],
    })
    planning_sources = [{
        "sourceChoice": "planning_source_1",
        "sourceArtifactRef": "artifact:prior-answer",
    }]

    semantic = build_adaptive_workflow_semantics(
        raw_input="Produce a current answer.",
        definition_catalog=catalog,
        workflow_planning_context=context,
        brief=brief,
        planning_sources=planning_sources,
    )

    assert len(semantic.work_items) == 1
    coverage = semantic.workflow_responsibility_coverage[0]
    assert coverage.coverage_mode == "planned_work"
    assert coverage.reused_result_refs == []


def test_explicit_v3_target_owners_do_not_collapse_distinct_outputs_to_terminal() -> None:
    from app.modules.research_design_planner_assembly import build_adaptive_workflow_semantics
    from app.modules.research_design_workflow_semantics import WorkflowProjectSemanticBriefV4

    owner_keys = ["records", "evidence", "recommendation"]
    catalog = [
        {"definitionId": f"opentrajectory:{key}", "definitionVersion": 1, "description": key, "artifactTypes": [f"{key}_artifact"]}
        for key in owner_keys
    ]
    context = {
        "schemaVersion": "workflow-planning-context.v2",
        "coreResponsibilities": [
            {"nodeKey": key, "definitionId": f"opentrajectory:{key}", "definitionVersion": 1, "displayOrder": index}
            for index, key in enumerate(owner_keys, start=1)
        ],
        "conditionalResponsibilities": [],
        "dependencies": [],
        "boundaryPolicy": {"allowedExternalActions": [], "prohibitedActions": []},
        "targetOutputSpecs": [
            {"description": output, "ownerNodeKey": owner}
            for output, owner in zip(
                ["traceable records", "evidence assessment", "next recommendation"],
                owner_keys,
                strict=True,
            )
        ],
    }
    brief = WorkflowProjectSemanticBriefV4.model_validate({
        "researchGoal": "Produce the three formal outputs.",
        "primaryResearchQuestions": ["What is supported?"],
        "auxiliaryResearchDimensions": [],
        "targetOutputs": ["traceable records", "evidence assessment", "next recommendation"],
        "constraints": [], "scientificUnknowns": [], "risksAndTradeoffs": [],
        "responsibilityDeltas": [
            {"responsibilityChoice": f"responsibility_{index}", "workItemScientificContexts": [key]}
            for index, key in enumerate(owner_keys, start=1)
        ],
    })

    semantic = build_adaptive_workflow_semantics(
        raw_input="Produce the three formal outputs.",
        definition_catalog=catalog,
        workflow_planning_context=context,
        brief=brief,
    )

    assert {row.local_key: row.target_output_refs for row in semantic.work_items} == {
        "records": ["traceable records"],
        "evidence": ["evidence assessment"],
        "recommendation": ["next recommendation"],
    }

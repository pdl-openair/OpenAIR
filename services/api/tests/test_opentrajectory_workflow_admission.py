from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import yaml

from app.models.external_action import ExternalActionKind
from app.modules.opentrajectory_workflow_admission import (
    OPENTRAJECTORY_SKILL_PACK,
    OPENTRAJECTORY_WORKFLOW_ID,
    opentrajectory_workflow_planning_admission,
)
from app.modules.research_design_workflow_semantic_provider import (
    WorkflowSemanticProviderConfig,
    prepare_hybrid_workflow_planner_attempt,
)
from app.modules.workflow_planning import workflow_planning_bundle


SNAPSHOT = {"schemaVersion": 1, "contentHash": "sha256:" + "e" * 64}


class _WorkflowDefinitionSource:
    def __init__(self) -> None:
        from app.modules.repo_paths import repo_root

        self._definitions: dict[tuple[str, int], SimpleNamespace] = {}
        for path in (repo_root() / "schemas" / "workflows").glob("*.yaml"):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            for node in payload["nodes"]:
                definition_id = str(node["definitionId"])
                definition_version = int(node["definitionVersion"])
                self._definitions[(definition_id, definition_version)] = SimpleNamespace(
                    definition_id=definition_id,
                    definition_version=definition_version,
                    substage_key=str(node["nodeKey"]),
                    stage_key="problem_modeling",
                    required_external_action_kinds=(),
                    external_action_contract_known=True,
                )

    def snapshot_ref(self) -> dict[str, Any]:
        return dict(SNAPSHOT)

    def resolve(self, definition_id: str):
        matches = [
            definition
            for (candidate_id, _version), definition in self._definitions.items()
            if candidate_id == definition_id
        ]
        return max(matches, key=lambda row: row.definition_version) if matches else None

    def resolve_version(self, definition_id: str, definition_version: int):
        return self._definitions.get((definition_id, definition_version))

    def planner_catalog_for_research_design(self, admission) -> tuple[dict[str, Any], ...]:
        workflow_snapshot = admission["workflowDefinitionSnapshot"]["definition"]
        rows = [
            {
                "definitionId": node["definitionId"],
                "definitionVersion": node["definitionVersion"],
                "pack": str(admission["skillPack"]),
                "stage": "problem_modeling",
                "label": node["nodeKey"],
                "description": node["nodeKey"],
                "planningApplicability": {
                    "scopeKind": "specialized",
                    "scopeLabel": str(admission["skillPack"]),
                },
                "instancePolicy": {"mode": "repeatable"},
                "requiredInputRefs": [],
                "artifactTypes": [],
                "criterionRefs": [],
                "contractComplete": False,
            }
            for node in workflow_snapshot["nodes"]
        ]
        rows.append({
            "definitionId": "sdr:report_generation",
            "definitionVersion": 1,
            "pack": "sdr",
            "stage": "insight_generation",
            "label": "report generation",
            "description": "assemble the final result",
            "planningApplicability": {
                "scopeKind": "general",
                "scopeLabel": "General research work",
            },
            "instancePolicy": {"mode": "singleton_collection"},
            "requiredInputRefs": [],
            "artifactTypes": [],
            "criterionRefs": [],
            "contractComplete": False,
        })
        return tuple(rows)




def test_ot_admission_uses_explicit_workflow_selection() -> None:
    selection = opentrajectory_workflow_planning_admission("使用 OpenTrajectory 研究 SEI")
    assert selection.workflow_id == OPENTRAJECTORY_WORKFLOW_ID


def test_ot_workflow_becomes_frozen_planner_constraints_not_a_researchdesign() -> None:
    source = _WorkflowDefinitionSource()
    selection = opentrajectory_workflow_planning_admission("使用 OpenTrajectory 研究 SEI")

    bundle = workflow_planning_bundle(
        selection=selection,
        raw_goal="使用 OpenTrajectory 研究 SEI",
        definition_source=source,
    )

    assert bundle is not None
    assert bundle.admission["workflowId"] == OPENTRAJECTORY_WORKFLOW_ID
    assert bundle.admission["workflowVersion"] == 4
    assert bundle.admission["skillPack"] == OPENTRAJECTORY_SKILL_PACK
    assert bundle.planning_context["schemaVersion"] == "workflow-planning-context.v2"
    assert bundle.planning_context["planningPolicy"] == {
        "responsibilityPlanning": "project_adaptive",
        "existingResultReuse": "validated_existing_results",
        "conditionalResponsibilities": "planner_selected",
    }
    required = [row["definitionId"] for row in bundle.planning_context["coreResponsibilities"]]
    optional = [row["definitionId"] for row in bundle.planning_context["conditionalResponsibilities"]]
    assert required == [
        "opentrajectory:evidence_assessment_synthesis",
        "opentrajectory:current_research_conclusion",
        "opentrajectory:key_unknowns_candidate_explanations",
        "opentrajectory:next_research_recommendation",
    ]
    assert optional == [
        "opentrajectory:research_record_establishment",
        "opentrajectory:mechanism_explanation",
    ]
    assert bundle.planning_context["targetOutputSpecs"] == [
        {"description": "可追溯的关键公开研究记录", "ownerNodeKey": "research_record_establishment"},
        {"description": "按条件比较的记录驱动证据评估与综合", "ownerNodeKey": "evidence_assessment_synthesis"},
        {"description": "当前结论、关键未知与最具判别力的下一步建议", "ownerNodeKey": "next_research_recommendation"},
    ]
    assert not {
        "opentrajectory:retrieval_strategy_design",
        "opentrajectory:source_acquisition",
        "opentrajectory:source_screening_selection",
        "opentrajectory:progressive_reading",
        "opentrajectory:findings_synthesis",
    } & set(required + optional)
    assert next(
        row for row in bundle.planning_context["dependencies"]
        if row["upstreamNodeKey"] == "mechanism_explanation"
        and row["downstreamNodeKey"] == "current_research_conclusion"
    )["dependencyType"] == "optional_enhancement"
    assert bundle.planning_context["boundaryPolicy"] == {
        "allowedExternalActions": [
            ExternalActionKind.EXTERNAL_READ.value,
            ExternalActionKind.EXTERNAL_COMPUTE.value,
        ],
        "confirmationRequiredActions": [],
        "prohibitedActions": [
            ExternalActionKind.EXTERNAL_WRITE.value,
            ExternalActionKind.PHYSICAL_ACTION.value,
        ],
        "scientificEvidenceBoundaries": [],
    }
    assert "sdr:report_generation" in {
        row["definitionId"] for row in bundle.definition_catalog
    }

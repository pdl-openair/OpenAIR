from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import yaml

from app.modules.pvd_workflow_admission import (
    PVD_SKILL_PACK,
    PVD_WORKFLOW_ID,
    pvd_workflow_planning_admission,
)
from app.modules.workflow_planning import workflow_planning_bundle


SNAPSHOT = {"schemaVersion": 1, "contentHash": "sha256:" + "9" * 64}


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
        members = [
            *workflow_snapshot["nodes"],
            *workflow_snapshot.get("supportingWork", []),
        ]
        return tuple({
            "definitionId": node["definitionId"],
            "definitionVersion": node["definitionVersion"],
            "pack": str(admission["skillPack"]),
            "stage": "problem_modeling",
            "label": node.get("nodeKey") or node["supportKey"],
            "description": node.get("nodeKey") or node["supportKey"],
            "planningApplicability": {
                "scopeKind": "specialized",
                "scopeLabel": str(admission["skillPack"]),
            },
            "instancePolicy": {"mode": "repeatable"},
            "requiredInputRefs": [],
            "artifactTypes": [],
            "criterionRefs": [],
            "contractComplete": False,
        } for node in members)






def test_explicit_pvd_workflow_selection_uses_canonical_identity() -> None:
    assert pvd_workflow_planning_admission().workflow_id == PVD_WORKFLOW_ID


def test_pvd_workflow_becomes_frozen_planner_constraints() -> None:
    source = _WorkflowDefinitionSource()
    selection = pvd_workflow_planning_admission()
    bundle = workflow_planning_bundle(
        selection=selection,
        raw_goal="优化 PVD Power、N₂、Ar 和 Deposition Time",
        definition_source=source,
    )

    assert bundle is not None
    assert bundle.admission["workflowId"] == PVD_WORKFLOW_ID
    assert bundle.admission["skillPack"] == PVD_SKILL_PACK
    assert bundle.admission["workflowVersion"] == 3
    core = [row["definitionId"] for row in bundle.planning_context["coreResponsibilities"]]
    conditional = [
        row["definitionId"] for row in bundle.planning_context["conditionalResponsibilities"]
    ]
    assert core == [
        "pvd:process_factor_candidate_experiment_analysis",
        "pvd:parameter_experiment_plan_feasibility_validation",
        "pvd:round_experiment_plan_parameter_recommendation",
    ]
    assert conditional == [
        "pvd:evidence_applicability_comparability_analysis",
        "pvd:experimental_feedback_round_analysis",
    ]
    assert bundle.planning_context["planningPolicy"] == {
        "responsibilityPlanning": "project_adaptive",
        "existingResultReuse": "validated_existing_results",
        "conditionalResponsibilities": "planner_selected",
    }
    optional_edges = {
        (row["upstreamNodeKey"], row["downstreamNodeKey"]): row["dependencyType"]
        for row in bundle.planning_context["dependencies"]
        if row["upstreamNodeKey"] in {
            "evidence_applicability_comparability_analysis",
            "experimental_feedback_round_analysis",
        }
    }
    assert optional_edges == {
        (
            "evidence_applicability_comparability_analysis",
            "process_factor_candidate_experiment_analysis",
        ): "optional_enhancement",
        (
            "experimental_feedback_round_analysis",
            "process_factor_candidate_experiment_analysis",
        ): "optional_enhancement",
    }

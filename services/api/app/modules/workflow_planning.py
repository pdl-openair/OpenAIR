"""Bind one explicit Workflow selection to the sole ResearchDesign Planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.models.workflow_definition import (
    FrozenWorkflowDefinition,
    FrozenWorkflowDefinitionV2,
    parse_frozen_workflow_definition,
)
from app.modules.production_workflow_source import ProductionWorkflowSource
from app.modules.workflow_planning_admission import WorkflowPlanningAdmission


@dataclass(frozen=True)
class WorkflowPlanningBundle:
    frozen_definition: FrozenWorkflowDefinition
    definition_catalog: tuple[dict[str, Any], ...]
    planning_context: Mapping[str, Any]
    admission: Mapping[str, Any]


def workflow_admission_for_selection(
    *,
    selection: WorkflowPlanningAdmission,
    definition_source: Any,
) -> Mapping[str, Any]:
    """Freeze the user's Workflow choice without producing ResearchDesign."""
    workflow_id = selection.workflow_id.strip()
    if not workflow_id:
        raise ValueError("Workflow selection is empty")

    workflow_source = ProductionWorkflowSource(definition_authority=definition_source)
    if workflow_source.resolve(workflow_id) is None:
        raise ValueError(f"Selected Workflow is not registered: {workflow_id}")
    frozen = workflow_source.freeze(workflow_id)
    definition = frozen.definition
    return {
        "workflowId": definition.workflow_id,
        "workflowVersion": definition.workflow_version,
        "workflowContentHash": frozen.content_hash,
        "workflowDefinitionSnapshot": frozen.model_dump(mode="json", by_alias=True),
        "skillPack": definition.skill_pack,
        "selectionReason": selection.reason,
    }


def _node_payload(node: Any) -> dict[str, Any]:
    return {
        "nodeKey": node.node_key,
        "definitionId": node.definition_id,
        "definitionVersion": node.definition_version,
        "displayOrder": node.display_order,
    }


def _dependency_payloads(definition: Any) -> list[dict[str, Any]]:
    node_by_key = {node.node_key: node for node in definition.nodes}
    return [
        {
            "upstreamNodeKey": dependency.upstream_node_key,
            "downstreamNodeKey": dependency.downstream_node_key,
            "upstreamDefinitionId": node_by_key[dependency.upstream_node_key].definition_id,
            "downstreamDefinitionId": node_by_key[dependency.downstream_node_key].definition_id,
            "dependencyType": dependency.dependency_type,
        }
        for dependency in definition.dependencies
    ]


def _supporting_work_payloads(definition: Any) -> list[dict[str, Any]]:
    rows = getattr(definition, "supporting_work", ())
    return [
        {
            "supportKey": row.support_key,
            "definitionId": row.definition_id,
            "definitionVersion": row.definition_version,
            "supportsNodeKey": row.supports_node_key,
            "dependsOnSupportKeys": list(row.depends_on_support_keys),
            "displayOrder": row.display_order,
        }
        for row in sorted(rows, key=lambda item: item.display_order)
    ]


def _planning_context(frozen: FrozenWorkflowDefinition) -> dict[str, Any]:
    definition = frozen.definition
    ordered = sorted(definition.nodes, key=lambda row: row.display_order)
    common = {
        "workflowId": definition.workflow_id,
        "workflowVersion": definition.workflow_version,
        "workflowContentHash": frozen.content_hash,
        "skillPack": definition.skill_pack,
        "description": definition.description,
        "dependencies": _dependency_payloads(definition),
        "boundaryPolicy": definition.boundary_policy.model_dump(mode="json", by_alias=True),
    }
    if isinstance(frozen, FrozenWorkflowDefinitionV2):
        context = {
            "schemaVersion": "workflow-planning-context.v2",
            **common,
            "planningPolicy": definition.planning_policy.model_dump(mode="json", by_alias=True),
            "supportingWork": _supporting_work_payloads(definition),
            "coreResponsibilities": [
                _node_payload(node) for node in ordered if node.requirement == "required"
            ],
            "conditionalResponsibilities": [
                _node_payload(node) for node in ordered if node.requirement == "optional"
            ],
        }
        if definition.target_output_specs:
            context["targetOutputSpecs"] = [
                row.model_dump(mode="json", by_alias=True)
                for row in definition.target_output_specs
            ]
        return context
    return {
        "schemaVersion": "workflow-planning-context.v1",
        **common,
        "requiredNodes": [
            _node_payload(node) for node in ordered if node.requirement == "required"
        ],
        "optionalNodes": [
            _node_payload(node) for node in ordered if node.requirement == "optional"
        ],
    }


def workflow_planning_bundle(
    *,
    selection: WorkflowPlanningAdmission | None,
    raw_goal: str,
    definition_source: Any,
) -> WorkflowPlanningBundle | None:
    del raw_goal  # Goal stays in the Planner request; Workflow contributes only frozen constraints.
    if selection is None:
        return None
    admission = workflow_admission_for_selection(
        selection=selection,
        definition_source=definition_source,
    )
    snapshot = admission["workflowDefinitionSnapshot"]
    if not isinstance(snapshot, Mapping):
        raise ValueError("Workflow snapshot is invalid")
    frozen = parse_frozen_workflow_definition(snapshot)
    definition = frozen.definition
    catalog = tuple(
        dict(row)
        for row in definition_source.planner_catalog_for_research_design(admission)
    )
    available = {
        (str(row["definitionId"]), int(row["definitionVersion"]))
        for row in catalog
    }
    required_definition_refs = [
        (node.definition_id, node.definition_version) for node in definition.nodes
    ]
    if isinstance(frozen, FrozenWorkflowDefinitionV2):
        required_definition_refs.extend(
            (row.definition_id, row.definition_version)
            for row in definition.supporting_work
        )
    missing = [
        definition_id
        for definition_id, definition_version in required_definition_refs
        if (definition_id, definition_version) not in available
    ]
    if missing:
        raise ValueError(
            "Workflow Definition is unavailable from the frozen Registry: " + ", ".join(missing)
        )

    return WorkflowPlanningBundle(
        frozen_definition=frozen,
        definition_catalog=catalog,
        planning_context=_planning_context(frozen),
        admission=admission,
    )


__all__ = ["WorkflowPlanningBundle", "workflow_admission_for_selection", "workflow_planning_bundle"]

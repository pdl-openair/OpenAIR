"""Deterministic assembly inside the sole ResearchDesign Planner.

A formally selected Workflow contributes its frozen research skeleton directly.
Requests without a formal Workflow may still receive model-supplied semantics.
The Compiler remains the deterministic checker for both paths.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.models.research_planner_semantic import PlannerSemanticProposalV2
from app.modules.research_design_workflow_semantics import (
    WorkflowProjectSemanticBriefV4,
    workflow_responsibility_choice,
)

_DETERMINISTIC_TARGET_OUTPUT = "形成与用户研究目标一致、可审阅且可追溯的研究结果。"

def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))



def _required_workflow_nodes(
    workflow_planning_context: Mapping[str, Any],
    definition_catalog: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str, Mapping[str, Any]]]:
    if workflow_planning_context.get("schemaVersion") != "workflow-planning-context.v1":
        raise ValueError("frozen Workflow planning context is invalid")
    required_nodes = workflow_planning_context.get("requiredNodes")
    if not isinstance(required_nodes, list) or not required_nodes:
        raise ValueError("frozen Workflow has no required planning nodes")

    catalog_by_identity = {
        (str(row.get("definitionId") or ""), int(row.get("definitionVersion") or 0)): row
        for row in definition_catalog
        if str(row.get("definitionId") or "")
    }
    ordered_nodes = sorted(
        required_nodes,
        key=lambda row: int(row.get("displayOrder") or 0) if isinstance(row, Mapping) else 0,
    )
    selected: list[tuple[str, str, Mapping[str, Any]]] = []
    seen_node_keys: set[str] = set()
    seen_definitions: set[tuple[str, int]] = set()
    for row in ordered_nodes:
        if not isinstance(row, Mapping):
            raise ValueError("frozen Workflow required node is invalid")
        node_key = str(row.get("nodeKey") or "").strip()
        definition_id = str(row.get("definitionId") or "").strip()
        definition_version = int(row.get("definitionVersion") or 0)
        identity = (definition_id, definition_version)
        if (
            not node_key
            or not definition_id
            or definition_version < 1
            or node_key in seen_node_keys
            or identity in seen_definitions
            or identity not in catalog_by_identity
        ):
            raise ValueError("frozen Workflow required node cannot be resolved uniquely")
        seen_node_keys.add(node_key)
        seen_definitions.add(identity)
        selected.append((node_key, definition_id, catalog_by_identity[identity]))
    return selected


def _required_workflow_dependencies(
    workflow_planning_context: Mapping[str, Any],
    *,
    selected_keys: set[str],
) -> tuple[dict[str, list[str]], set[str]]:
    dependencies = workflow_planning_context.get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("frozen Workflow dependencies are invalid")
    upstream_by_downstream: dict[str, list[str]] = {key: [] for key in selected_keys}
    has_selected_downstream: set[str] = set()
    for dependency in dependencies:
        if not isinstance(dependency, Mapping):
            raise ValueError("frozen Workflow dependency is invalid")
        upstream = str(dependency.get("upstreamNodeKey") or "").strip()
        downstream = str(dependency.get("downstreamNodeKey") or "").strip()
        if upstream in selected_keys and downstream in selected_keys:
            if upstream not in upstream_by_downstream[downstream]:
                upstream_by_downstream[downstream].append(upstream)
            has_selected_downstream.add(upstream)
    return upstream_by_downstream, has_selected_downstream


def _workflow_work_items(
    selected: Sequence[tuple[str, str, Mapping[str, Any]]],
    *,
    upstream_by_downstream: Mapping[str, Sequence[str]],
    target_owner: str,
) -> list[dict[str, Any]]:
    work_items: list[dict[str, Any]] = []
    for node_key, definition_id, definition in selected:
        objective = str(definition.get("description") or definition.get("label") or "").strip()
        if not objective:
            objective = f"完成 {node_key} 对应的研究责任。"
        work_items.append({
            "localKey": node_key,
            "objective": objective,
            "definitionId": definition_id,
            "inputRefs": [],
            "expectedArtifactTypes": list(definition.get("artifactTypes") or ()),
            "targetOutputRefs": [_DETERMINISTIC_TARGET_OUTPUT] if node_key == target_owner else [],
            "dependsOnLocalKeys": list(upstream_by_downstream[node_key]),
        })
    return work_items


def build_deterministic_workflow_semantics(
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
) -> PlannerSemanticProposalV2:
    """Build a minimum confirmable draft from facts owned by a selected Workflow."""
    goal = str(raw_input or "").strip()
    if not goal:
        raise ValueError("research goal is required for Workflow planning")

    selected = _required_workflow_nodes(workflow_planning_context, definition_catalog)
    selected_keys = {row[0] for row in selected}
    upstream_by_downstream, has_selected_downstream = _required_workflow_dependencies(
        workflow_planning_context,
        selected_keys=selected_keys,
    )
    terminal_keys = [key for key, *_rest in selected if key not in has_selected_downstream]
    target_owner = terminal_keys[-1] if terminal_keys else selected[-1][0]
    semantic = PlannerSemanticProposalV2.model_validate({
        "schemaVersion": "planner-semantic.v2",
        "goal": goal,
        "understanding": {
            "userIntent": goal,
            "materialSummary": None,
            "targetOutputs": [_DETERMINISTIC_TARGET_OUTPUT],
            "evidenceBoundaries": [
                "研究结论仅使用实际执行后获得并可追溯的证据；当前仅形成待确认方案。"
            ],
            "missingInputs": [],
            "riskNotes": [
                "当前为待确认的研究方案，尚未执行检索、分析、计算或验证，也未形成研究结论。"
            ],
        },
        "constraints": [],
        "requestedExternalActions": [],
        "workItems": _workflow_work_items(
            selected,
            upstream_by_downstream=upstream_by_downstream,
            target_owner=target_owner,
        ),
        "targetRequirements": [],
    })
    return semantic


def build_adaptive_workflow_semantics(
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    brief: WorkflowProjectSemanticBriefV4,
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> PlannerSemanticProposalV2:
    """Compile a V2 responsibility Workflow into project-adaptive work items.

    Workflow responsibilities remain server-owned. The Provider supplies only
    project-specific work contexts, conditional entry, and a Provider-local reuse
    choice. Definition identity, formal result refs, dependencies, output ownership,
    provenance, and boundary policy remain deterministic.
    """
    if workflow_planning_context.get("schemaVersion") != "workflow-planning-context.v2":
        raise ValueError("adaptive Workflow planning requires workflow-planning-context.v2")
    raw_goal = str(raw_input or "").strip()
    if not raw_goal:
        raise ValueError("research goal is required for Workflow planning")

    core = workflow_planning_context.get("coreResponsibilities")
    conditional = workflow_planning_context.get("conditionalResponsibilities")
    if not isinstance(core, list) or not core or not isinstance(conditional, list):
        raise ValueError("adaptive Workflow responsibility catalog is invalid")
    responsibility_rows: list[tuple[Mapping[str, Any], str]] = [
        *[(row, "core") for row in core if isinstance(row, Mapping)],
        *[(row, "conditional") for row in conditional if isinstance(row, Mapping)],
    ]
    responsibility_rows.sort(key=lambda pair: int(pair[0].get("displayOrder") or 0))
    if len(responsibility_rows) != len(core) + len(conditional):
        raise ValueError("adaptive Workflow responsibility entry is invalid")
    responsibilities = [
        (node, requirement, workflow_responsibility_choice(position))
        for position, (node, requirement) in enumerate(responsibility_rows, start=1)
    ]
    valid_choices = {choice for _node, _requirement, choice in responsibilities}
    from app.modules.planner_semantic_content import coalesce_responsibility_deltas

    deltas_by_choice = coalesce_responsibility_deltas(
        brief.responsibility_deltas, valid_choices, brief.content_notes,
    )

    final_core_node = max(
        (row for row in core if isinstance(row, Mapping)),
        key=lambda row: int(row.get("displayOrder") or 0),
    )
    final_core_key = str(final_core_node.get("nodeKey") or "")

    source_by_choice = {
        str(source.get("sourceChoice") or "").strip(): source
        for source in planning_sources
    }
    if (
        len(source_by_choice) != len(planning_sources)
        or "" in source_by_choice
    ):
        raise ValueError("planning source context contains invalid choices")
    source_decision_by_choice = {
        decision.source_choice: decision
        for decision in brief.planning_source_uses
    }

    catalog_by_identity = {
        (str(row.get("definitionId") or ""), int(row.get("definitionVersion") or 0)): row
        for row in definition_catalog
        if str(row.get("definitionId") or "")
    }
    work_items: list[dict[str, Any]] = []
    work_keys_by_node: dict[str, list[str]] = {}
    reuse_refs_by_node: dict[str, list[str]] = {}
    coverage: list[dict[str, Any]] = []
    local_key_counter: set[str] = set()

    for node, requirement, choice in responsibilities:
        node_key = str(node.get("nodeKey") or "").strip()
        definition_id = str(node.get("definitionId") or "").strip()
        definition_version = int(node.get("definitionVersion") or 0)
        definition = catalog_by_identity.get((definition_id, definition_version))
        if not node_key or definition is None:
            raise ValueError("adaptive Workflow responsibility Definition cannot be resolved")
        delta = deltas_by_choice.get(choice)

        contexts: list[str] | None = None
        reused_ref: str | None = None
        if delta is None and requirement == "core":
            contexts = []
            rationale = (
                "Workflow core responsibility retained by code; the model supplied "
                "no additional project-specific context."
            )
        elif delta is None:
            rationale = (
                "Conditional responsibility omitted because no project-specific "
                "scientific delta selected it."
            )
        elif delta.reuse_candidate_key is not None and node_key == final_core_key:
            contexts = []
            rationale = (
                "Terminal core responsibility retained as current project work; "
                "the reuse suggestion cannot replace the current answer."
            )
        elif delta.reuse_candidate_key is not None:
            candidate_key = delta.reuse_candidate_key.strip()
            source = source_by_choice.get(candidate_key)
            usage_decision = source_decision_by_choice.get(candidate_key)
            source_artifact_ref = (
                str(source.get("sourceArtifactRef") or "").strip()
                if source is not None
                else ""
            )
            if (
                source is not None
                and usage_decision is not None
                and usage_decision.adopted_unit_numbers
                and source_artifact_ref
            ):
                reused_ref = source_artifact_ref
                rationale = (
                    "Provider selected an eligible prior-result candidate; code restored "
                    "its formal artifact reference."
                )
            elif requirement == "core":
                contexts = []
                rationale = (
                    "Workflow core responsibility retained as current work because "
                    "the suggested reuse candidate was not formally usable."
                )
            else:
                rationale = (
                    "Conditional responsibility omitted because its suggested reuse "
                    "candidate was not formally usable."
                )
        else:
            contexts = [
                value.strip()
                for value in (delta.work_item_scientific_contexts or ())
            ]

            rationale = (
                "Workflow core responsibility received project-specific scientific work."
                if requirement == "core"
                else "Conditional responsibility selected by project-specific scientific need."
            )

        if reused_ref is not None:
            work_keys: list[str] = []
            reuse_refs_by_node[node_key] = [reused_ref]
            coverage_mode = "reused_result"
        elif contexts is None:
            work_keys = []
            reuse_refs_by_node[node_key] = []
            coverage_mode = "not_selected"
        else:
            work_keys = []
            # Contexts refine one registered research responsibility; they are
            # not independent subgoal identities or execution tasks.
            context = "；".join(_dedupe(contexts))
            local_key = node_key
            if local_key in local_key_counter:
                raise ValueError("adaptive Workflow generated duplicate local work key")
            local_key_counter.add(local_key)
            base_objective = (
                str(definition.get("description") or definition.get("label") or "")
                .strip()
                .rstrip("。")
            )
            objective = (
                f"{base_objective}。当前项目科研重点：{context}。"
                if context and base_objective
                else context or base_objective
            )
            work_items.append({
                "localKey": local_key,
                "objective": objective,
                "definitionId": definition_id,
                "inputRefs": ["research_goal"],
                "expectedArtifactTypes": list(definition.get("artifactTypes") or ()),
                "targetOutputRefs": [],
                "dependsOnLocalKeys": [],
            })
            work_keys.append(local_key)
            reuse_refs_by_node[node_key] = []
            coverage_mode = "planned_work"
        work_keys_by_node[node_key] = work_keys
        coverage.append({
            "responsibilityKey": node_key,
            "definitionId": definition_id,
            "requirement": requirement,
            "coverageMode": coverage_mode,
            "workItemLocalKeys": work_keys,
            "reusedResultRefs": list(reuse_refs_by_node[node_key]),
            "rationale": rationale,
        })

    # The terminal core responsibility is code-owned and always produces this answer.
    final_keys = work_keys_by_node.get(final_core_key) or []
    if not final_keys:
        raise ValueError("terminal core Workflow responsibility was not materialized")
    by_local_key = {row["localKey"]: row for row in work_items}

    supporting_work = workflow_planning_context.get("supportingWork") or []
    if not isinstance(supporting_work, list):
        raise ValueError("adaptive Workflow supporting work is invalid")
    support_rows_by_node: dict[str, list[Mapping[str, Any]]] = {}
    for row in supporting_work:
        if not isinstance(row, Mapping):
            raise ValueError("adaptive Workflow supporting work entry is invalid")
        support_rows_by_node.setdefault(str(row.get("supportsNodeKey") or ""), []).append(row)

    for supported_node_key, rows in support_rows_by_node.items():
        supported_keys = work_keys_by_node.get(supported_node_key) or []
        if not supported_keys:
            continue
        rows = sorted(rows, key=lambda row: int(row.get("displayOrder") or 0))
        support_local_by_key: dict[str, str] = {}
        depended_support_keys: set[str] = set()
        for row in rows:
            support_key = str(row.get("supportKey") or "").strip()
            definition_id = str(row.get("definitionId") or "").strip()
            definition_version = int(row.get("definitionVersion") or 0)
            definition = catalog_by_identity.get((definition_id, definition_version))
            dependency_keys = [str(value).strip() for value in row.get("dependsOnSupportKeys") or ()]
            if not support_key or definition is None or any(not value for value in dependency_keys):
                raise ValueError("adaptive Workflow supporting work cannot be resolved")
            dependency_locals = []
            for dependency_key in dependency_keys:
                dependency_local = support_local_by_key.get(dependency_key)
                if dependency_local is None:
                    raise ValueError("adaptive Workflow supporting work dependency is unavailable")
                dependency_locals.append(dependency_local)
                depended_support_keys.add(dependency_key)
            local_key = f"support__{supported_node_key}__{support_key}"
            if local_key in local_key_counter:
                raise ValueError("adaptive Workflow generated duplicate supporting work key")
            local_key_counter.add(local_key)
            base_objective = str(definition.get("description") or definition.get("label") or "").strip().rstrip("。")
            objective = (
                f"{base_objective}。为 {supported_node_key} 准备当前项目所需的正式研究材料。"
                if base_objective
                else f"为 {supported_node_key} 准备当前项目所需的正式研究材料。"
            )
            support_item = {
                "localKey": local_key,
                "objective": objective,
                "definitionId": definition_id,
                "inputRefs": ["research_goal"],
                "expectedArtifactTypes": list(definition.get("artifactTypes") or ()),
                "targetOutputRefs": [],
                "dependsOnLocalKeys": dependency_locals,
            }
            work_items.append(support_item)
            by_local_key[local_key] = support_item
            support_local_by_key[support_key] = local_key

        terminal_support_locals = [
            local_key
            for support_key, local_key in support_local_by_key.items()
            if support_key not in depended_support_keys
        ]
        for supported_key in supported_keys:
            supported_item = by_local_key[supported_key]
            supported_item["dependsOnLocalKeys"] = list(dict.fromkeys([
                *supported_item["dependsOnLocalKeys"],
                *terminal_support_locals,
            ]))

    dependencies = workflow_planning_context.get("dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("adaptive Workflow dependencies are invalid")
    for dependency in dependencies:
        if not isinstance(dependency, Mapping):
            raise ValueError("adaptive Workflow dependency is invalid")
        upstream = str(dependency.get("upstreamNodeKey") or "").strip()
        downstream = str(dependency.get("downstreamNodeKey") or "").strip()
        downstream_keys = work_keys_by_node.get(downstream) or []
        if not downstream_keys:
            continue
        upstream_keys = work_keys_by_node.get(upstream) or []
        upstream_reuse = reuse_refs_by_node.get(upstream) or []
        for downstream_key in downstream_keys:
            row = by_local_key[downstream_key]
            if upstream_keys:
                row["dependsOnLocalKeys"] = list(dict.fromkeys([
                    *row["dependsOnLocalKeys"],
                    *upstream_keys,
                ]))
            elif upstream_reuse:
                row["inputRefs"] = list(dict.fromkeys([
                    *row["inputRefs"],
                    *upstream_reuse,
                ]))

    from app.modules.target_output_ownership import (
        assemble_target_outputs,
        attach_reuse_acceptance_work,
    )

    target_outputs, target_output_owners, target_output_contributors = assemble_target_outputs(
        context=workflow_planning_context, brief=brief, responsibilities=responsibilities,
        work_keys_by_node=work_keys_by_node, work_items=by_local_key, final_key=final_core_key,
    )

    attach_reuse_acceptance_work(
        target_outputs, target_output_owners, coverage, by_local_key[final_keys[-1]],
        contributors=target_output_contributors,
    )
    reused_descriptions = [
        f"{row['responsibilityKey']}←{','.join(row['reusedResultRefs'])}"
        for row in coverage
        if row["coverageMode"] == "reused_result"
    ]
    boundary_policy = workflow_planning_context.get("boundaryPolicy")
    if not isinstance(boundary_policy, Mapping):
        raise ValueError("adaptive Workflow boundary policy is invalid")
    goal = brief.research_goal.strip() or raw_goal

    semantic = PlannerSemanticProposalV2.model_validate({
        "schemaVersion": "planner-semantic.v2",
        "goal": goal,
        "understanding": {
            "userIntent": goal,
            "materialSummary": (
                "当前方案显式复用既有正式结果：" + "；".join(reused_descriptions)
                if reused_descriptions
                else None
            ),
            "primaryResearchQuestions": _dedupe(brief.primary_research_questions) or [raw_goal],
            "auxiliaryResearchDimensions": _dedupe(brief.auxiliary_research_dimensions),
            "targetOutputs": target_outputs,
            "evidenceBoundaries": [
                str(value).strip()
                for value in (boundary_policy.get("scientificEvidenceBoundaries") or ())
                if str(value).strip()
            ],
            "missingInputs": [],
            "riskNotes": _dedupe([
                *brief.content_notes,
                *(f"待研究未知：{value.strip()}" for value in brief.scientific_unknowns),
                *(f"风险与取舍：{value.strip()}" for value in brief.risks_and_tradeoffs),
            ]),
        },
        "constraints": list(brief.constraints),
        "requestedExternalActions": [],
        "workItems": work_items,
        "targetRequirements": [],
        "workflowResponsibilityCoverage": coverage,
        "targetOutputOwners": target_output_owners,
        "targetOutputContributors": target_output_contributors,
    })
    return semantic


__all__ = ["build_adaptive_workflow_semantics", "build_deterministic_workflow_semantics"]

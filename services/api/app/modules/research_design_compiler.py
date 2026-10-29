"""Deterministic compiler from planner semantics to ResearchDesignProposal.

The compiler owns all mechanical identifiers, graph references, coverage paths,
boundaries, criteria, evaluator bindings, planning-depth claims, and semantic contract validation. It never invents or appends a research
responsibility.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.research_planner_semantic import (
    PlannerSemanticProposalV2,
)
from app.modules.target_output_ownership import (
    target_requirement_modes as _target_requirement_modes,
)
from app.modules.research_design_service import (
    ResearchDesignProposal,
    ResearchDesignProposalError,
    validate_research_design_proposal,
)


MAX_COMPILED_ACCEPTANCE_CRITERIA = 2048
MAX_COMPILED_PROPOSAL_BYTES = 1_500_000


class ResearchDesignCompilerError(ResearchDesignProposalError):
    def __init__(
        self,
        message: str,
        *,
        safe_error_code: str,
        safe_field_paths: tuple[str, ...],
        repair_context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            safe_error_code=safe_error_code,
            safe_field_paths=safe_field_paths,
        )
        self.safe_repair_context = dict(repair_context or {})


@dataclass(frozen=True)
class _DefinitionContract:
    definition_id: str
    definition_version: int
    stage: str
    required_input_refs: tuple[str, ...]
    artifact_types: tuple[str, ...]
    criterion_refs: tuple[str, ...]
    instance_policy: str


def _stable_token(prefix: str, *parts: object, length: int = 16) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:length]}"


def _stable_instance_key(local_key: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", local_key.strip()).strip("_")
    if cleaned and len(cleaned) <= 72:
        return cleaned
    return _stable_token("item", local_key, length=20)


def _freeze_catalog(catalog: Sequence[Mapping[str, Any]]) -> tuple[_DefinitionContract, ...]:
    contracts: list[_DefinitionContract] = []
    seen: set[str] = set()
    for index, row in enumerate(catalog):
        try:
            definition_id = str(row["definitionId"]).strip()
            definition_version = int(row["definitionVersion"])
            stage = str(row["stage"]).strip()
            required_inputs = tuple(str(value).strip() for value in row.get("requiredInputRefs") or ())
            artifacts = tuple(str(value).strip() for value in row.get("artifactTypes") or ())
            evaluator_source = row.get("evaluatorRefs") if "evaluatorRefs" in row else row.get("criterionRefs")
            evaluators = tuple(str(value).strip() for value in evaluator_source or ())
            policy = str((row.get("instancePolicy") or {"mode": "repeatable"}).get("mode") or "")
            contract_complete = bool(row.get(
                "contractComplete",
                bool(required_inputs and artifacts and evaluators),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchDesignCompilerError(
                "frozen Definition catalog is invalid",
                safe_error_code="research_design_definition_catalog_invalid",
                safe_field_paths=(f"/availableDefinitions/{index}",),
            ) from exc
        if (
            not definition_id
            or definition_id in seen
            or definition_version < 1
            or not stage
            or any(not value for value in (*required_inputs, *artifacts, *evaluators))
            or policy not in {"repeatable", "singleton_collection"}
            or (contract_complete and (not required_inputs or not artifacts or not evaluators))
        ):
            raise ResearchDesignCompilerError(
                "frozen Definition catalog is incomplete",
                safe_error_code="research_design_definition_catalog_invalid",
                safe_field_paths=(f"/availableDefinitions/{index}",),
            )
        seen.add(definition_id)
        contracts.append(_DefinitionContract(
            definition_id=definition_id,
            definition_version=definition_version,
            stage=stage,
            required_input_refs=required_inputs,
            artifact_types=artifacts,
            criterion_refs=evaluators,
            instance_policy=policy,
        ))
    if not contracts:
        raise ResearchDesignCompilerError(
            "frozen Definition catalog is required",
            safe_error_code="research_design_definition_catalog_invalid",
            safe_field_paths=("/availableDefinitions",),
        )
    return tuple(contracts)


def _planned_artifact_type(contract: _DefinitionContract) -> str:
    """Create an honest instance-level output envelope for semantic-only Definitions.

    This is not written back into the canonical Definition and therefore cannot
    masquerade as missing ontology truth.  It exists only so a selected research
    responsibility can form a valid confirmable ResearchDesign instance.
    """
    definition_token = re.sub(r"[^a-zA-Z0-9_.-]+", ".", contract.definition_id).strip(".")
    return f"{definition_token or 'research'}.planned_output"


def _normalize_definition_id(value: str, contracts: Sequence[_DefinitionContract]) -> str:
    exact = {row.definition_id: row.definition_id for row in contracts}
    if value in exact:
        return value
    folded = [row.definition_id for row in contracts if row.definition_id.casefold() == value.casefold()]
    if len(folded) == 1:
        return folded[0]
    return value


def _topological_keys(graph: Mapping[str, tuple[str, ...]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(key: str) -> None:
        if key in visiting:
            raise ResearchDesignCompilerError(
                "semantic dependency graph contains a cycle",
                safe_error_code="planner_semantic_dependency_conflict",
                safe_field_paths=("/workItems",),
            )
        if key in visited:
            return
        visiting.add(key)
        for upstream in graph[key]:
            visit(upstream)
        visiting.remove(key)
        visited.add(key)
        ordered.append(key)

    for key in graph:
        visit(key)
    return ordered


def _risk_level(score: int) -> str:
    return "high" if score >= 3 else "medium" if score >= 1 else "low"


def _criterion(
    *,
    instance_key: str,
    source_path: str,
    kind: str,
    description: str,
    artifact_type: str | None,
    evaluator_ref: str | None,
) -> dict[str, Any]:
    key = _stable_token("criterion", instance_key, source_path, kind, artifact_type, evaluator_ref)
    evaluator_type = "agent" if evaluator_ref is not None else "deterministic"
    return {
        "criterionKey": key,
        "description": description,
        "criterionKind": kind,
        "coverageSourcePaths": [source_path] if source_path else [],
        "requiredArtifactType": artifact_type,
        "judgmentRequirement": "domain_professional" if evaluator_ref is not None else "deterministic",
        "evaluatorType": evaluator_type,
        "evaluatorRef": evaluator_ref,
    }


def _append_unique_criterion(
    criteria: list[dict[str, Any]],
    seen_keys: set[str],
    item: dict[str, Any],
) -> bool:
    key = str(item["criterionKey"])
    if key in seen_keys:
        return False
    seen_keys.add(key)
    criteria.append(item)
    return True


def _workflow_prohibited_actions(
    workflow_planning_context: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Read a registered policy without revalidating the frozen Workflow per task."""
    if not isinstance(workflow_planning_context, Mapping):
        return ()
    boundary_policy = workflow_planning_context.get("boundaryPolicy")
    if not isinstance(boundary_policy, Mapping):
        return ()
    return tuple(
        str(value).strip()
        for value in boundary_policy.get("prohibitedActions") or ()
        if str(value).strip()
    )


def compile_research_design_proposal(
    semantic: PlannerSemanticProposalV2,
    *,
    definition_catalog: Sequence[Mapping[str, Any]],
    raw_input: str,
    workflow_planning_context: Mapping[str, Any] | None = None,
) -> ResearchDesignProposal:
    contracts = _freeze_catalog(definition_catalog)
    by_id = {row.definition_id: row for row in contracts}
    from app.modules.target_output_ownership import validate_semantic_target_owners

    validate_semantic_target_owners(semantic, workflow_planning_context)
    target_requirement_modes = _target_requirement_modes(semantic)

    normalized_definition_ids = [
        _normalize_definition_id(row.definition_id, contracts)
        for row in semantic.work_items
    ]
    unknown_paths = tuple(
        f"/workItems/{index}/definitionId"
        for index, value in enumerate(normalized_definition_ids)
        if value not in by_id
    )
    if unknown_paths:
        raise ResearchDesignCompilerError(
            "semantic proposal selected a Definition outside the frozen catalog",
            safe_error_code="research_design_definition_unknown",
            safe_field_paths=unknown_paths,
            repair_context={
                "repairKind": "focused_semantic",
                "schemaVersion": "planner-semantic-repair.v2",
                "semanticProposal": semantic.model_dump(mode="json", by_alias=True),
                "fieldPaths": list(unknown_paths),
                "definitionCandidates": [row.definition_id for row in contracts],
            },
        )

    workflow_prohibited_actions = _workflow_prohibited_actions(workflow_planning_context)

    local_keys = [row.local_key for row in semantic.work_items]
    instance_key_by_local = {value: _stable_instance_key(value) for value in local_keys}
    if len(set(instance_key_by_local.values())) != len(instance_key_by_local):
        indexes_by_instance: dict[str, list[int]] = {}
        for index, local_key in enumerate(local_keys):
            indexes_by_instance.setdefault(instance_key_by_local[local_key], []).append(index)
        affected_indexes = sorted(
            index
            for indexes in indexes_by_instance.values()
            if len(indexes) > 1
            for index in indexes
        )
        paths = tuple(f"/workItems/{index}/localKey" for index in affected_indexes)
        raise ResearchDesignCompilerError(
            "semantic local keys collapse to the same stable instance key",
            safe_error_code="planner_semantic_local_key_conflict",
            safe_field_paths=paths,
            repair_context={
                "repairKind": "focused_semantic",
                "schemaVersion": "planner-semantic-repair.v2",
                "semanticProposal": semantic.model_dump(mode="json", by_alias=True),
                "fieldPaths": list(paths),
                "definitionCandidates": [row.definition_id for row in contracts],
                "graphIssues": [{
                    "issueKind": "duplicate_local_key",
                    "affectedLocalKeys": [local_keys[index] for index in affected_indexes],
                    "affectedEdges": [],
                    "affectedOutputRefs": [],
                    "workItems": [
                        {"index": index, "localKey": local_keys[index]}
                        for index in affected_indexes
                    ],
                    "fieldPaths": list(paths),
                }],
            },
        )

    singleton_counts: dict[str, int] = {}
    for definition_id in normalized_definition_ids:
        if by_id[definition_id].instance_policy == "singleton_collection":
            singleton_counts[definition_id] = singleton_counts.get(definition_id, 0) + 1
    conflicting_singletons = {key for key, count in singleton_counts.items() if count > 1}
    if conflicting_singletons:
        paths = tuple(
            f"/workItems/{index}/definitionId"
            for index, definition_id in enumerate(normalized_definition_ids)
            if definition_id in conflicting_singletons
        )
        raise ResearchDesignCompilerError(
            "singleton Definition was selected by multiple semantic work items",
            safe_error_code="planner_semantic_definition_cardinality_conflict",
            safe_field_paths=paths,
            repair_context={
                "repairKind": "focused_semantic",
                "schemaVersion": "planner-semantic-repair.v2",
                "semanticProposal": semantic.model_dump(mode="json", by_alias=True),
                "fieldPaths": list(paths),
                "definitionCandidates": [row.definition_id for row in contracts],
                "graphIssues": [{
                    "issueKind": "singleton_definition_conflict",
                    "affectedLocalKeys": [
                        semantic.work_items[index].local_key
                        for index, definition_id in enumerate(normalized_definition_ids)
                        if definition_id in conflicting_singletons
                    ],
                    "affectedEdges": [],
                    "affectedOutputRefs": [],
                    "workItems": [
                        {"index": index, "localKey": semantic.work_items[index].local_key}
                        for index, definition_id in enumerate(normalized_definition_ids)
                        if definition_id in conflicting_singletons
                    ],
                    "fieldPaths": list(paths),
                }],
            },
        )

    graph = {
        row.local_key: tuple(row.depends_on_local_keys)
        for row in semantic.work_items
    }
    ordered_local_keys = _topological_keys(graph)
    sequence_by_local = {key: index for index, key in enumerate(ordered_local_keys)}

    output_paths = {
        value: f"understanding.targetOutputs[{index}]"
        for index, value in enumerate(semantic.understanding.target_outputs)
    }
    constraint_paths = [f"constraints[{index}]" for index, _ in enumerate(semantic.constraints)]
    evidence_paths = [
        f"understanding.evidenceBoundaries[{index}]"
        for index, _ in enumerate(semantic.understanding.evidence_boundaries)
    ]
    external_paths = [
        f"externalActionBoundary.confirmationRequiredKinds[{index}]"
        for index, _ in enumerate(semantic.requested_external_actions)
    ]
    server_prohibited_actions = list(dict.fromkeys([
        "execute research work before ResearchDesign confirmation",
        "expand the user-defined research scope without confirmation",
        *workflow_prohibited_actions,
    ]))
    risk_paths = [f"riskBoundary.prohibitedActions[{index}]" for index in range(len(server_prohibited_actions))]
    human_gate_paths = [
        f"riskBoundary.humanGateTriggers[{index}]"
        for index, _ in enumerate(semantic.requested_external_actions)
    ]

    global_guards = [
        *((path, "constraint_compliance", semantic.constraints[pos]) for pos, path in enumerate(constraint_paths)),
        *((path, "evidence_boundary_compliance", semantic.understanding.evidence_boundaries[pos]) for pos, path in enumerate(evidence_paths)),
        *((path, "external_action_guard", semantic.requested_external_actions[pos]) for pos, path in enumerate(external_paths)),
        *((path, "risk_guard", server_prohibited_actions[pos]) for pos, path in enumerate(risk_paths)),
        *((path, "risk_guard", semantic.requested_external_actions[pos]) for pos, path in enumerate(human_gate_paths)),
    ]

    instances: list[dict[str, Any]] = []
    criteria_by_source: dict[str, list[tuple[str, str]]] = {}
    for index, row in enumerate(semantic.work_items):
        definition_id = normalized_definition_ids[index]
        contract = by_id[definition_id]
        provider_artifacts = list(dict.fromkeys(row.expected_artifact_types))
        requested_artifacts = provider_artifacts if contract.artifact_types else []
        invalid_artifacts = [
            value for value in requested_artifacts if value not in contract.artifact_types
        ]
        if invalid_artifacts:
            path = f"/workItems/{index}/expectedArtifactTypes"
            raise ResearchDesignCompilerError(
                "semantic expectedArtifactTypes are outside the frozen Definition contract",
                safe_error_code="planner_semantic_artifact_contract_conflict",
                safe_field_paths=(path,),
                repair_context={
                    "repairKind": "focused_semantic",
                    "schemaVersion": "planner-semantic-repair.v2",
                    "semanticProposal": semantic.model_dump(mode="json", by_alias=True),
                    "fieldPaths": [path],
                    "artifactCandidatesByDefinition": {contract.definition_id: list(contract.artifact_types)},
                },
            )
        expected_artifacts = (
            requested_artifacts
            or list(contract.artifact_types)
            or [_planned_artifact_type(contract)]
        )
        evaluator_refs = sorted(contract.criterion_refs)
        instance_key = instance_key_by_local[row.local_key]
        criteria: list[dict[str, Any]] = []
        seen_criterion_keys: set[str] = set()
        primary_artifact = expected_artifacts[0]

        # Output ownership and Definition quality are separate layers. Complete
        # Definition contracts remain authoritative. For semantic-only Definitions,
        # compile deterministic instance-level output-presence criteria without
        # inventing evaluator refs or mutating canonical Definition truth.
        for output in row.target_output_refs:
            source_path = output_paths[output]
            requirement = target_requirement_modes.get((row.local_key, output))
            requirement_mode = requirement[0] if requirement is not None else None
            if requirement_mode == "artifact_presence":
                target_evaluators: list[str | None] = [None]
                criterion_kind = "target_output"
                description = f"Produce the planned artifact for the requested output: {output}"
            elif requirement_mode == "professional_judgment":
                if not evaluator_refs:
                    requirement_index = requirement[1]
                    field_path = f"/targetRequirements/{requirement_index}/completionMode"
                    raise ResearchDesignCompilerError(
                        "professional target requirement has no Definition-authorized evaluator",
                        safe_error_code="planner_semantic_professional_evaluator_unavailable",
                        safe_field_paths=(field_path,),
                        repair_context={
                            "repairKind": "focused_semantic",
                            "schemaVersion": "planner-semantic-repair.v2",
                            "semanticProposal": semantic.model_dump(mode="json", by_alias=True),
                            "fieldPaths": [field_path],
                            "definitionCandidates": [row.definition_id for row in contracts],
                            "failedTargetRequirement": {
                                "index": requirement_index,
                                "workItemLocalKey": row.local_key,
                                "targetOutputRef": output,
                                "completionMode": "professional_judgment",
                                "definitionId": contract.definition_id,
                            },
                            "definitionContractFacts": {
                                contract.definition_id: {
                                    "criterionRefs": list(contract.criterion_refs),
                                    "artifactTypes": list(contract.artifact_types),
                                    "requiredInputRefs": list(contract.required_input_refs),
                                }
                            },
                        },
                    )
                if len(evaluator_refs) > 1:
                    requirement_index = requirement[1]
                    raise ResearchDesignCompilerError(
                        "professional target requirement has ambiguous Definition-authorized evaluators",
                        safe_error_code="planner_semantic_professional_evaluator_ambiguous",
                        safe_field_paths=(
                            f"/targetRequirements/{requirement_index}/completionMode",
                        ),
                    )
                target_evaluators = evaluator_refs
                criterion_kind = "target_output"
                description = (
                    "Apply Definition-authorized professional judgment to the requested output: "
                    f"{output}"
                )
            else:
                target_evaluators = evaluator_refs or [None]
                criterion_kind = "target_output"
                description = (
                    f"Produce an accepted artifact for the requested output: {output}"
                    if evaluator_refs
                    else f"Produce the planned artifact for the requested output: {output}"
                )
            for evaluator_ref in target_evaluators:
                item = _criterion(
                    instance_key=instance_key,
                    source_path=source_path,
                    kind=criterion_kind,
                    description=description,
                    artifact_type=primary_artifact,
                    evaluator_ref=evaluator_ref,
                )
                if _append_unique_criterion(criteria, seen_criterion_keys, item):
                    criteria_by_source.setdefault(source_path, []).append(
                        (instance_key, f"{instance_key}:{item['criterionKey']}")
                    )

        # TargetRequirement controls only the query-specific target criterion.
        # The Definition-owned quality contract is independent and must remain
        # authoritative for every WorkItem that declares evaluators.
        if evaluator_refs:
            for artifact in expected_artifacts:
                for evaluator_ref in evaluator_refs:
                    _append_unique_criterion(
                        criteria,
                        seen_criterion_keys,
                        _criterion(
                            instance_key=instance_key,
                            source_path="",
                            kind="artifact_quality",
                            description=f"Validate the Definition-owned quality contract for {artifact}.",
                            artifact_type=artifact,
                            evaluator_ref=evaluator_ref,
                        ),
                    )
        elif not criteria:
            # Intermediate semantic work items may own no final target output.
            # Give them one deterministic, instance-only completion envelope so
            # ResearchDesign remains structurally valid without pretending that a
            # missing canonical acceptance contract exists.
            _append_unique_criterion(
                criteria,
                seen_criterion_keys,
                _criterion(
                    instance_key=instance_key,
                    source_path="",
                    kind="artifact_quality",
                    description="Produce the planned output for this research responsibility.",
                    artifact_type=primary_artifact,
                    evaluator_ref=None,
                ),
            )

        # Global constraints and gates are shared design-level obligations.
        # Persist them once on the first topological instance rather than
        # cloning the same criterion onto every WorkItem.
        if row.local_key == ordered_local_keys[0]:
            for source_path, kind, description in global_guards:
                item = _criterion(
                    instance_key=instance_key,
                    source_path=source_path,
                    kind=kind,
                    description=f"Respect the active boundary: {description}",
                    artifact_type=None,
                    evaluator_ref=None,
                )
                if _append_unique_criterion(criteria, seen_criterion_keys, item):
                    criteria_by_source.setdefault(source_path, []).append(
                        (instance_key, f"{instance_key}:{item['criterionKey']}")
                    )

        dependencies = [
            {
                "dependencyKey": _stable_token(
                    "dependency",
                    instance_key,
                    instance_key_by_local[upstream],
                ),
                "upstreamInstanceKey": instance_key_by_local[upstream],
                "conditionType": "instance_completed",
                "requiredArtifactTypes": [],
                "confirmationTargetRef": None,
            }
            for upstream in row.depends_on_local_keys
        ]
        instances.append({
            "instanceKey": instance_key,
            "definitionId": definition_id,
            "objective": row.objective,
            "inputRefs": list(dict.fromkeys([*row.input_refs, *contract.required_input_refs])),
            "expectedArtifactTypes": expected_artifacts,
            "acceptanceCriteria": criteria,
            "dependencies": dependencies,
            "sequenceHint": sequence_by_local[row.local_key],
        })

    all_source_paths = [
        *output_paths.values(),
        *constraint_paths,
        *evidence_paths,
        *external_paths,
        *risk_paths,
        *human_gate_paths,
    ]
    from app.modules.target_output_ownership import non_work_target_paths, validate_target_owners

    target_output_owners = {
        output_paths[output]: owner
        for output, owner in semantic.target_output_owners.items()
        if output in output_paths
    }
    target_output_contributors = {
        output_paths[output]: list(keys)
        for output, keys in semantic.target_output_contributors.items()
    }
    validate_target_owners(
        target_output_owners,
        semantic.understanding.target_outputs,
        semantic.workflow_responsibility_coverage,
        target_output_contributors,
    )
    non_work_paths = non_work_target_paths(
        target_output_owners, semantic.workflow_responsibility_coverage,
        target_output_contributors,
    )
    uncovered = [
        path
        for path in all_source_paths
        if path not in criteria_by_source and path not in non_work_paths
        and path not in output_paths.values()
    ]
    if uncovered:
        raise ResearchDesignCompilerError(
            "semantic proposal leaves required target outputs without a work item",
            safe_error_code="planner_semantic_target_mapping_missing",
            safe_field_paths=("/workItems",),
            repair_context={
                "repairKind": "focused_semantic",
                "schemaVersion": "planner-semantic-repair.v2",
                "semanticProposal": semantic.model_dump(mode="json", by_alias=True),
                "fieldPaths": ["/workItems"],
                "missingTargetOutputs": [
                    value for value, path in output_paths.items() if path in uncovered
                ],
            },
        )

    criterion_count = sum(len(instance["acceptanceCriteria"]) for instance in instances)
    if criterion_count > MAX_COMPILED_ACCEPTANCE_CRITERIA:
        raise ResearchDesignCompilerError(
            "compiled acceptance criteria exceed the deterministic safety limit",
            safe_error_code="research_design_compiler_scale_limit",
            safe_field_paths=("/workItems",),
        )

    candidates = [
        {
            "sourcePath": path,
            "instanceKeys": list(
                dict.fromkeys(instance for instance, _ in criteria_by_source.get(path, []))
            ),
            "acceptanceCriterionKeys": list(
                dict.fromkeys(ref for _, ref in criteria_by_source.get(path, []))
            ),
            "rationale": "Deterministically compiled from semantic target ownership and server boundary policy.",
        }
        for path in all_source_paths
    ]

    stages = {by_id[value].stage for value in normalized_definition_ids}
    dependency_count = sum(len(value) for value in graph.values())
    evidence_score = len(semantic.understanding.evidence_boundaries) + max(0, len(semantic.understanding.target_outputs) - 1)
    uncertainty_score = len(semantic.understanding.missing_inputs) + len(semantic.understanding.risk_notes)
    external_score = len(semantic.requested_external_actions)
    problem_clarity = "high" if not semantic.understanding.missing_inputs else (
        "low" if len(semantic.understanding.missing_inputs) >= 3 else "medium"
    )
    complexity = {
        "problemClarity": problem_clarity,
        "expectedStageSpan": "one" if len(stages) == 1 else "multiple",
        "dependencyComplexity": _risk_level(dependency_count - 1),
        "evidenceBurden": _risk_level(evidence_score - 1),
        "externalActionRisk": _risk_level(external_score),
        "researchUncertainty": _risk_level(uncertainty_score),
        "rationale": "Server-computed from the compiled Definition graph, evidence boundaries, external actions, and missing inputs.",
    }
    factors = (
        complexity["dependencyComplexity"],
        complexity["evidenceBurden"],
        complexity["externalActionRisk"],
        complexity["researchUncertainty"],
    )
    lightweight = (
        len(instances) == 1
        and len(stages) == 1
        and problem_clarity == "high"
        and all(value == "low" for value in factors)
    )
    sdr = (
        len(instances) >= 4
        or len(stages) >= 3
        or sum(value == "high" for value in factors) >= 2
    )
    planning_mode = "lightweight" if lightweight else "sdr" if sdr else "partial"

    workflow_responsibility_coverage = [
        {
            "responsibilityKey": row.responsibility_key,
            "definitionId": row.definition_id,
            "requirement": row.requirement,
            "coverageMode": row.coverage_mode,
            "instanceKeys": [instance_key_by_local[key] for key in row.work_item_local_keys],
            "reusedResultRefs": list(row.reused_result_refs),
            "rationale": row.rationale,
        }
        for row in semantic.workflow_responsibility_coverage
    ]

    proposal_payload = {
        "planningMode": planning_mode,
        "planningComplexity": complexity,
        "goal": semantic.goal,
        "targetOutputOwners": target_output_owners,
        "targetOutputContributors": target_output_contributors,
        "understanding": {
            "researchType": None,
            "userIntent": semantic.understanding.user_intent,
            "materialSummary": semantic.understanding.material_summary,
            "primaryResearchQuestions": list(semantic.understanding.primary_research_questions),
            "auxiliaryResearchDimensions": list(semantic.understanding.auxiliary_research_dimensions),
            "targetOutputs": list(semantic.understanding.target_outputs),
            "evidenceBoundaries": list(semantic.understanding.evidence_boundaries),
            "missingInputs": list(semantic.understanding.missing_inputs),
            "riskNotes": list(semantic.understanding.risk_notes),
            "planningRationale": "Compiled from planner semantics against the frozen Definition catalog.",
        },
        "constraints": list(semantic.constraints),
        "scopeBoundary": {
            "allowedObjectiveKinds": [f"substage_definition:{value}" for value in dict.fromkeys(normalized_definition_ids)],
            "excludedActions": list(server_prohibited_actions),
            "allowedMaterialRefs": list(dict.fromkeys(
                ref for instance in instances for ref in instance["inputRefs"]
            )),
            "maxSubstageInstances": len(instances),
        },
        "budgetBoundary": {
            "maxModelCalls": max(8, len(instances) * 8 + sum(
                1
                for instance in instances
                for criterion in instance["acceptanceCriteria"]
                if criterion["evaluatorType"] == "agent"
            )),
            "maxToolCalls": max(8, len(instances) * 4 + sum(
                len(instance["expectedArtifactTypes"]) * 4
                for instance in instances
            )),
            "maxWallTimeSec": min(604800, max(1800, len(instances) * 3600 + dependency_count * 600)),
            "maxCostAmount": None,
            "currency": None,
        },
        "externalActionBoundary": {
            "allowedActionKinds": list(semantic.requested_external_actions),
            "confirmationRequiredKinds": list(semantic.requested_external_actions),
            "allowedDestinations": [],
        },
        "riskBoundary": {
            "maxRiskClass": "medium" if semantic.requested_external_actions or semantic.understanding.risk_notes else "low",
            "prohibitedActions": list(server_prohibited_actions),
            "humanGateTriggers": list(semantic.requested_external_actions),
        },
        "instances": instances,
        "workflowResponsibilityCoverage": workflow_responsibility_coverage,
        "coverageClaimCandidates": candidates,
    }
    serialized_size = len(
        json.dumps(
            proposal_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    if serialized_size > MAX_COMPILED_PROPOSAL_BYTES:
        raise ResearchDesignCompilerError(
            "compiled ResearchDesign exceeds the deterministic serialized-size limit",
            safe_error_code="research_design_compiler_scale_limit",
            safe_field_paths=("/workItems",),
        )

    return validate_research_design_proposal(
        proposal_payload,
        allowed_definition_ids=frozenset(by_id),
        definition_stage_by_id={key: value.stage for key, value in by_id.items()},
    )


__all__ = [
    "MAX_COMPILED_ACCEPTANCE_CRITERIA",
    "MAX_COMPILED_PROPOSAL_BYTES",
    "ResearchDesignCompilerError",
    "compile_research_design_proposal",
]

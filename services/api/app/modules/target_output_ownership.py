"""Frozen output ownership joined with project responsibility coverage.

Names and prose never determine an owner. Query-specific ownership is a small
scientific association supplied using Provider-local output numbers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.execution.research_start_processing import PlannerSemanticInputError
from app.models.research_orchestration import ProjectCompletionRequirement
from app.models.research_planner_semantic import PlannerSemanticProposalV2


def _mapping_error(message: str) -> PlannerSemanticInputError:
    return PlannerSemanticInputError(
        message,
        error_code="planner_target_output_owner_unresolved",
        field_paths=("/targetOutputs", "/responsibilityDeltas"),
    )


def _frozen_output_owners(context: Mapping[str, Any], node_keys: set[str]) -> dict[str, str]:
    specs = context.get("targetOutputSpecs") or []
    if not isinstance(specs, list):
        raise ValueError("Workflow target-output specifications are invalid")
    owners: dict[str, str] = {}
    for spec in specs:
        if not isinstance(spec, Mapping):
            raise ValueError("Workflow target-output specification is invalid")
        output = str(spec.get("description") or "").strip()
        owner = str(spec.get("ownerNodeKey") or "")
        if not output or owner not in node_keys or output in owners:
            raise ValueError("Workflow target-output ownership cannot be resolved uniquely")
        owners[output] = owner
    return owners


def _query_associations(brief: Any, node_by_choice: Mapping[str, str]) -> dict[int, list[str]]:
    contributors: dict[int, list[str]] = {}
    for delta in brief.responsibility_deltas:
        owner = node_by_choice.get(delta.responsibility_choice)
        for number in delta.target_output_numbers:
            if owner is None or number < 1 or number > len(brief.target_outputs):
                brief.content_notes.append(
                    f"目标关联尚未映射：职责 {delta.responsibility_choice}，目标编号 {number}。"
                )
                continue
            values = contributors.setdefault(number, [])
            if owner not in values:
                values.append(owner)
    return contributors


def assemble_target_outputs(
    *,
    context: Mapping[str, Any],
    brief: Any,
    responsibilities: Sequence[tuple[Mapping[str, Any], str, str]],
    work_keys_by_node: Mapping[str, list[str]],
    work_items: Mapping[str, dict[str, Any]],
    final_key: str,
) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    from app.modules.planner_target_associations import attach_query_contributors

    node_by_choice = {choice: str(node["nodeKey"]) for node, _req, choice in responsibilities}
    owners = _frozen_output_owners(context, set(node_by_choice.values()))
    outputs = list(dict.fromkeys([*owners, *brief.target_outputs]))
    for output, owner in owners.items():
        for key in work_keys_by_node[owner]:
            work_items[key]["targetOutputRefs"].append(output)
    contributors = attach_query_contributors(
        brief=brief, owners=owners,
        contributors=_query_associations(brief, node_by_choice),
        work_keys_by_node=work_keys_by_node, work_items=work_items,
    )
    if not outputs:
        output = "形成与用户研究目标一致、可审阅且可追溯的研究结果。"
        outputs.append(output)
        owners[output] = final_key
        for key in work_keys_by_node[final_key]:
            work_items[key]["targetOutputRefs"].append(output)
    return outputs, owners, contributors


def validate_target_owners(
    owners: Mapping[str, str], outputs: Sequence[str], coverage: Sequence[Any],
    contributors: Mapping[str, list[str]] | None = None,
) -> None:
    paths = {f"understanding.targetOutputs[{index}]" for index, _ in enumerate(outputs)}
    responsibilities = {row.responsibility_key: row for row in coverage}
    if any(path not in paths or owner not in responsibilities for path, owner in owners.items()):
        raise ValueError("target output references an unknown Workflow responsibility")
    for path, keys in (contributors or {}).items():
        if path not in paths or not keys or len(set(keys)) != len(keys):
            raise ValueError(
                "target contributions require a known output and distinct responsibilities"
            )
        if any(key not in responsibilities for key in keys):
            raise ValueError("target contributions reference an unknown Workflow responsibility")
        if path in owners and keys != [owners[path]]:
            raise ValueError("target contributions cannot change its frozen owner")


def non_work_target_paths(
    owners: Mapping[str, str], coverage: Sequence[Any],
    contributors: Mapping[str, list[str]] | None = None,
) -> set[str]:
    modes = {row.responsibility_key: row.coverage_mode for row in coverage}
    paths = {
        path
        for path, owner in owners.items()
        if modes.get(owner) in {"not_selected", "reused_result"}
    }
    paths.update(
        path for path, keys in (contributors or {}).items()
        if keys and all(modes.get(key) in {"not_selected", "reused_result"} for key in keys)
    )
    return paths


def project_completion_requirements(
    *,
    design_id: str,
    target_outputs: list[str],
    target_output_owners: Mapping[str, str],
    id_factory: Callable[..., str],
    target_output_contributors: Mapping[str, list[str]] | None = None,
) -> list[ProjectCompletionRequirement]:
    occurrences: dict[str, int] = {}
    requirements = []
    for index, output in enumerate(target_outputs):
        occurrence = occurrences.get(output, 0)
        occurrences[output] = occurrence + 1
        path = f"understanding.targetOutputs[{index}]"
        requirements.append(
            ProjectCompletionRequirement(
                completion_requirement_id=id_factory(
                    "project-completion", design_id, output, occurrence
                ),
                target_output_ref=path,
                description=output,
                workflow_responsibility_key=target_output_owners.get(path),
                contributing_responsibility_keys=(target_output_contributors or {}).get(path),
            )
        )
    return requirements


def validate_frozen_target_owners(proposal: Any, admission: Mapping[str, Any] | None) -> None:
    if admission is None:
        if not proposal.target_output_owners:
            return
        raise ValueError("Workflow target ownership requires a frozen admission")
    specs = admission["workflowDefinitionSnapshot"]["definition"].get("targetOutputSpecs", [])
    paths_by_output = {
        value: f"understanding.targetOutputs[{index}]"
        for index, value in enumerate(proposal.understanding.target_outputs)
    }
    for spec in specs:
        path = paths_by_output.get(spec["description"])
        if path is None or proposal.target_output_owners.get(path) != spec["ownerNodeKey"]:
            raise ValueError("proposal changed frozen Workflow output ownership")


def validate_semantic_target_owners(semantic: Any, context: Mapping[str, Any] | None) -> None:
    coverage = {row.responsibility_key: row for row in semantic.workflow_responsibility_coverage}
    work_owners: dict[str, set[str]] = {}
    for row in semantic.work_items:
        for output in row.target_output_refs:
            work_owners.setdefault(output, set()).add(row.local_key)
    for output, owner in semantic.target_output_owners.items():
        if output not in semantic.understanding.target_outputs or owner not in coverage:
            raise ValueError("semantic target output has no valid Workflow owner")
        expected = set(coverage[owner].work_item_local_keys)
        if work_owners.get(output, set()) != expected:
            raise ValueError("semantic target output work items conflict with its Workflow owner")
    for output, keys in semantic.target_output_contributors.items():
        if output not in semantic.understanding.target_outputs or not keys:
            raise ValueError("target contributions require a known output and responsibilities")
        if len(set(keys)) != len(keys) or any(key not in coverage for key in keys):
            raise ValueError("target contributions must name distinct current responsibilities")
        owner = semantic.target_output_owners.get(output)
        if owner is not None and keys != [owner]:
            raise ValueError("target contributions cannot change its frozen owner")
        expected = {local for key in keys for local in coverage[key].work_item_local_keys}
        if work_owners.get(output, set()) != expected:
            raise ValueError("target work items must cover every current contribution")
    for spec in (context or {}).get("targetOutputSpecs", []):
        if semantic.target_output_owners.get(spec["description"]) != spec["ownerNodeKey"]:
            raise ValueError("semantic proposal changed frozen Workflow output ownership")


def target_requirement_modes(
    semantic: PlannerSemanticProposalV2,
) -> dict[tuple[str, str], tuple[str, int]]:
    """Validate semantic target requirements even for programmatically built DTOs."""

    from app.modules.research_design_compiler import ResearchDesignCompilerError

    known_work_items = {row.local_key: row for row in semantic.work_items}
    known_outputs = set(semantic.understanding.target_outputs)
    modes: dict[tuple[str, str], tuple[str, int]] = {}
    invalid_paths: list[str] = []
    for index, requirement in enumerate(semantic.target_requirements):
        path = f"/targetRequirements/{index}"
        work_item = known_work_items.get(requirement.work_item_local_key)
        if work_item is None:
            invalid_paths.append(f"{path}/workItemLocalKey")
            continue
        if requirement.target_output_ref not in known_outputs:
            invalid_paths.append(f"{path}/targetOutputRef")
            continue
        if requirement.target_output_ref not in work_item.target_output_refs:
            invalid_paths.append(f"{path}/targetOutputRef")
            continue
        pair = (requirement.work_item_local_key, requirement.target_output_ref)
        if pair in modes:
            invalid_paths.append(f"{path}/targetOutputRef")
            continue
        modes[pair] = (requirement.completion_mode, index)
    if invalid_paths:
        raise ResearchDesignCompilerError(
            "semantic target requirement does not reference an owned target output",
            safe_error_code="planner_semantic_target_requirement_invalid",
            safe_field_paths=tuple(invalid_paths),
        )
    if semantic.target_requirements:
        owned_pairs = {
            (work_item.local_key, target_output)
            for work_item in semantic.work_items
            for target_output in work_item.target_output_refs
        }
        if missing_pairs := owned_pairs - set(modes):
            raise ResearchDesignCompilerError(
                "non-empty semantic target requirements must cover every owned target output",
                safe_error_code="planner_semantic_target_requirement_incomplete",
                safe_field_paths=("/targetRequirements",),
                repair_context={
                    "missingTargetRequirements": [
                        {"workItemLocalKey": work_item_key, "targetOutputRef": target_output}
                        for work_item_key, target_output in sorted(missing_pairs)
                    ],
                },
            )
    return modes


def revision_coverage_maps(
    latest_design: Any,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    from app.modules.research_design_service import ResearchDesignRevisionSaveUnavailable

    coverage = latest_design.coverage_report
    if coverage is None:
        raise ResearchDesignRevisionSaveUnavailable(
            "当前研究方案缺少可验证的覆盖记录，请重新加载后再试。"
        )
    source_path_by_target_id = {row.target_id: row.source_path for row in coverage.targets}
    instance_key_by_id = {
        row.substage_instance_id: f"existing_instance__{row.substage_instance_id}"
        for row in latest_design.instances
    }
    criterion_owner: dict[str, str] = {}
    for instance in latest_design.instances:
        instance_key = instance_key_by_id[instance.substage_instance_id]
        for index, criterion in enumerate(instance.acceptance_criteria):
            # The proposal key is an internal compiler input, not persisted authority.
            # Use a position-stable key so repeated targeted revisions do not churn it.
            criterion_owner[criterion.criterion_id] = f"{instance_key}:preserved_criterion_{index}"
    return source_path_by_target_id, instance_key_by_id, criterion_owner


def attach_reuse_acceptance_work(
    outputs: list[str],
    owners: Mapping[str, str],
    coverage: Sequence[Mapping[str, Any]],
    terminal_work: dict[str, Any],
    contributors: Mapping[str, list[str]] | None = None,
) -> None:
    """An existing synthesis work can assess applicability without rerunning the owner."""
    import json

    reused = {
        row["responsibilityKey"]: row for row in coverage if row["coverageMode"] == "reused_result"
    }
    requests: list[dict[str, Any]] = [
        {
            "targetOutputRef": f"understanding.targetOutputs[{index}]",
            "description": output,
            "responsibilityKey": owner,
            "reusedResultRefs": list(reused[owner]["reusedResultRefs"]),
        }
        for index, output in enumerate(outputs)
        for owner in (contributors or {}).get(output, [owners[output]] if output in owners else [])
        if owner in reused
    ]
    if not requests:
        return
    terminal_work["inputRefs"] = list(
        dict.fromkeys(
            [
                *terminal_work["inputRefs"],
                *(ref for row in requests for ref in row["reusedResultRefs"]),
            ]
        )
    )
    terminal_work["objective"] += (
        "。检查所提供复用材料是否满足当前目标。仅在真实材料足以支持时，在结构化产物的 "
        "targetOutputReuseJudgments 数组中逐项记录 targetOutputRef、responsibilityKey、"
        "reusedResultRefs、verdict=accepted 和非空 rationale；材料缺失或不足时不接受，"
        "并保留不确定性。规划采用材料不等于接受。待核查目标："
        + json.dumps(requests, ensure_ascii=False)
    )


def revision_target_metadata(
    latest_design: Any, instance_key_by_id: Mapping[str, str]
) -> dict[str, Any]:
    """Preserve current Workflow ownership in a targeted design revision."""
    return {
        "targetOutputOwners": {
            row.target_output_ref: row.workflow_responsibility_key
            for row in latest_design.project_completion_requirements
            if row.workflow_responsibility_key is not None
        },
        "targetOutputContributors": {
            row.target_output_ref: list(row.contributing_responsibility_keys)
            for row in latest_design.project_completion_requirements
            if row.contributing_responsibility_keys is not None
        },
        "workflowResponsibilityCoverage": [
            {
                "responsibilityKey": row.responsibility_key,
                "definitionId": row.definition_id,
                "requirement": row.requirement,
                "coverageMode": row.coverage_mode,
                "instanceKeys": [
                    instance_key_by_id[ref.substage_instance_id] for ref in row.instance_refs
                ],
                "reusedResultRefs": list(row.reused_result_refs),
                "rationale": row.rationale,
            }
            for row in latest_design.workflow_responsibility_coverage
        ],
    }


def legacy_revision_context(
    latest_design: Any, admission: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Recognize missing historical fields, never repair a partial current mapping."""
    from app.models.workflow_definition import parse_frozen_workflow_definition
    from app.modules.research_design_service import ResearchDesignRevisionSaveUnavailable
    from app.modules.workflow_planning import _planning_context

    if any(
        "workflow_responsibility_key" in row.model_fields_set
        for row in latest_design.project_completion_requirements
    ):
        return None
    if admission is None:
        return None
    frozen = parse_frozen_workflow_definition(admission["workflowDefinitionSnapshot"])
    context = _planning_context(frozen)
    provenance = latest_design.workflow_provenance_ref
    if provenance is None or (
        provenance.workflow_id != frozen.workflow_id
        or provenance.workflow_version != frozen.workflow_version
        or provenance.content_hash != "sha256:" + frozen.content_hash
    ):
        raise ResearchDesignRevisionSaveUnavailable(
            "当前方案与项目保存的研究流程不一致，无法安全应用修改。原方案已保留。"
        )
    return context if context.get("targetOutputSpecs") else None


def restore_legacy_revision_targets(
    payload: dict[str, Any],
    *,
    context: Mapping[str, Any],
    definition_catalog: Sequence[Mapping[str, Any]],
    query_owner_provider: Callable[[Mapping[str, Any]], Mapping[str, str]],
) -> None:
    """Compile a new draft's target criteria from frozen owners and scientific associations."""
    outputs = payload["understanding"]["targetOutputs"]
    coverage = payload["workflowResponsibilityCoverage"]
    owners = _frozen_output_owners(context, {row["responsibilityKey"] for row in coverage})
    extras = set(outputs) - set(owners)
    if extras:
        associations = query_owner_provider(context)
        if set(associations) != set(outputs):
            raise _mapping_error("Historical query targets need complete scientific associations")
        for output in extras:
            owners[output] = associations[output]
        if any(associations[output] != owner for output, owner in owners.items()):
            raise _mapping_error("Historical query association conflicts with frozen ownership")
    if set(owners) != set(outputs):
        raise _mapping_error("Historical revision must preserve every frozen target")
    compiled = _compile_legacy_revision_targets(payload, owners, context, definition_catalog)
    _replace_legacy_target_criteria(payload, compiled)
    payload["targetOutputOwners"] = compiled["targetOutputOwners"]


def _compile_legacy_revision_targets(
    payload: Mapping[str, Any],
    owners: Mapping[str, str],
    context: Mapping[str, Any],
    definition_catalog: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    from app.modules.research_design_compiler import compile_research_design_proposal

    local_by_key = {
        row["instanceKey"]: f"revision_{index}" for index, row in enumerate(payload["instances"])
    }
    coverage = [
        {
            **{key: value for key, value in row.items() if key != "instanceKeys"},
            "workItemLocalKeys": [local_by_key[key] for key in row["instanceKeys"]],
        }
        for row in payload["workflowResponsibilityCoverage"]
    ]
    outputs_by_key: dict[str, list[str]] = {key: [] for key in local_by_key}
    for output, owner in owners.items():
        owner_row = next(
            (
                row
                for row in payload["workflowResponsibilityCoverage"]
                if row["responsibilityKey"] == owner
            ),
            None,
        )
        if owner_row is None:
            raise _mapping_error("Historical query association has an unknown responsibility")
        for key in owner_row["instanceKeys"]:
            outputs_by_key[key].append(output)
    understanding = payload["understanding"]
    semantic = PlannerSemanticProposalV2.model_validate(
        {
            "schemaVersion": "planner-semantic.v2",
            "goal": payload["goal"],
            "understanding": {
                key: understanding[key]
                for key in (
                    "userIntent",
                    "materialSummary",
                    "primaryResearchQuestions",
                    "auxiliaryResearchDimensions",
                    "targetOutputs",
                    "evidenceBoundaries",
                    "missingInputs",
                    "riskNotes",
                )
            },
            "constraints": payload["constraints"],
            "targetOutputOwners": dict(owners),
            "workflowResponsibilityCoverage": coverage,
            "workItems": [
                {
                    "localKey": local_by_key[row["instanceKey"]],
                    "objective": row["objective"],
                    "definitionId": row["definitionId"],
                    "inputRefs": row["inputRefs"],
                    "expectedArtifactTypes": row["expectedArtifactTypes"],
                    "targetOutputRefs": outputs_by_key[row["instanceKey"]],
                    "dependsOnLocalKeys": [
                        local_by_key[dep["upstreamInstanceKey"]] for dep in row["dependencies"]
                    ],
                }
                for row in payload["instances"]
            ],
        }
    )
    return compile_research_design_proposal(
        semantic,
        definition_catalog=definition_catalog,
        raw_input=payload["goal"],
        workflow_planning_context=context,
    ).model_dump(mode="json", by_alias=True)


def _replace_legacy_target_criteria(payload: dict[str, Any], compiled: Mapping[str, Any]) -> None:
    paths = set(compiled["targetOutputOwners"])
    key_map = {
        new["instanceKey"]: old["instanceKey"]
        for old, new in zip(payload["instances"], compiled["instances"], strict=True)
    }
    for old, new in zip(payload["instances"], compiled["instances"], strict=True):
        # Boundary and Definition-quality criteria retain their historical contract.
        preserved = [
            row
            for row in old["acceptanceCriteria"]
            if not paths.intersection(row["coverageSourcePaths"])
        ]
        targets = [
            row
            for row in new["acceptanceCriteria"]
            if paths.intersection(row["coverageSourcePaths"])
        ]
        old["acceptanceCriteria"] = [*preserved, *targets]
    candidates = []
    for row in compiled["coverageClaimCandidates"]:
        if row["sourcePath"] not in paths:
            continue
        candidates.append(
            {
                **row,
                "instanceKeys": [key_map[key] for key in row["instanceKeys"]],
                "acceptanceCriterionKeys": [
                    key_map[ref.split(":", 1)[0]] + ":" + ref.split(":", 1)[1]
                    for ref in row["acceptanceCriterionKeys"]
                ],
            }
        )
    payload["coverageClaimCandidates"] = [
        *[row for row in payload["coverageClaimCandidates"] if row["sourcePath"] not in paths],
        *candidates,
    ]

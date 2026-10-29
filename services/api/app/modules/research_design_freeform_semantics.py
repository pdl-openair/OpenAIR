"""Provider-facing scientific semantics for freeform ResearchDesign planning.

The LLM decides research meaning and selects among provider-local research-work
choices. Formal Definition IDs, local keys, input references, artifact types,
output references, and dependency references are assembled deterministically by
code before the existing ResearchDesign compiler validates the formal result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.execution.research_start_processing import PlannerSemanticInputError
from app.models.research_planner_semantic import PlannerSemanticProposalV2
from app.modules.planner_semantic_content import (
    indexed_target_outputs,
    receive_goal,
    receive_texts,
    remap_target_numbers,
)
from app.modules.research_planning_sources import (
    PlanningSourceUseDecision,
    planning_source_prompt_rules,
    planning_sources_for_provider,
)
from app.modules.research_planning_stage_framework import research_stage_framework


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _ProviderSemanticModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="ignore",
        populate_by_name=True,
        strict=False,
    )


_DEFAULT_TARGET_OUTPUT = "形成与用户研究目标一致、可审阅且可追溯的研究结果。"


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(text for item in value if (text := _text(item))))


def _numbers(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            item
            for item in value
            if isinstance(item, int) and not isinstance(item, bool) and item > 0
        )
    )


class FreeformScientificWorkItemV1(_ProviderSemanticModel):
    scientific_objective: str = ""
    work_type_choice: str = ""
    target_output_numbers: list[int] = []
    prerequisite_work_item_numbers: list[int] = []


class FreeformProjectScientificBriefV1(_ProviderSemanticModel):
    content_notes: list[str] = []
    research_goal: str = ""
    material_summary: str | None = None
    primary_research_questions: list[str] = []
    auxiliary_research_dimensions: list[str] = []
    target_outputs: list[str] = []
    evidence_boundaries: list[str] = []
    missing_inputs: list[str] = []
    risk_notes: list[str] = []
    constraints: list[str] = []
    requested_external_actions: list[str] = []
    work_items: list[FreeformScientificWorkItemV1] = []
    planning_source_uses: list[PlanningSourceUseDecision] = []


def _work_type_choices(definition_catalog: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    for index, row in enumerate(definition_catalog, start=1):
        applicability = row.get("planningApplicability")
        if not isinstance(applicability, Mapping):
            raise ValueError("research-work planning applicability is unavailable")
        choices.append(
            {
                "choice": f"work_type_{index}",
                "stage": str(row.get("stage") or ""),
                "label": str(row.get("label") or ""),
                "description": str(row.get("description") or ""),
                "professionalScope": {
                    "scopeKind": str(applicability.get("scopeKind") or ""),
                    "scopeLabel": str(applicability.get("scopeLabel") or ""),
                    "scopeDescription": str(applicability.get("scopeDescription") or ""),
                    "whenToUse": str(applicability.get("whenToUse") or ""),
                },
            }
        )
    return choices


def freeform_scientific_brief_schema(
    definition_catalog: Sequence[Mapping[str, Any]],
    *,
    include_planning_sources: bool = False,
) -> dict[str, Any]:
    """Keep Provider output as a loose JSON object; mapping is server-owned."""
    del definition_catalog
    schema = FreeformProjectScientificBriefV1.model_json_schema(by_alias=True)
    schema["properties"].pop("contentNotes", None)
    if not include_planning_sources:
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("planningSourceUses", None)
    return schema


def freeform_scientific_brief_prompts(
    *,
    raw_input: str,
    source_page: str,
    selected_hint: str | None,
    definition_catalog: Sequence[Mapping[str, Any]],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    system_prompt = """You are the scientific-semantic Planner for an OpenAIR ResearchDesign draft.
Return one JSON object with useful scientific contents: researchGoal (text), materialSummary
(text when useful), primaryResearchQuestions, auxiliaryResearchDimensions, targetOutputs,
evidenceBoundaries, missingInputs, riskNotes, constraints and requestedExternalActions (text lists).
Use workItems with scientificObjective, workTypeChoice, targetOutputNumbers and
prerequisiteWorkItemNumbers. Optional contents may be omitted; do not fill a formal system form.

Your authority is scientific meaning only:
- select useful registered research-work types, then write their query-specific scientific
objectives;
- identify only the goal, questions, outputs, boundaries, unknowns, risks, and constraints that help
answer this request;
- relate selected work to outputs and to scientific prerequisites when that relation is useful.

Machine structure is outside your authority:
- do not create or copy Definition IDs, Definition versions, local keys, instance IDs, input refs,
artifact refs, target-output strings inside work items, dependency keys, evaluator refs, criterion
keys, provenance, execution authority, Agents, Skills, WorkUnits, or Dispatch facts;
- workTypeChoice is only a provider-local selector from availableResearchWorkTypes; the server maps
it to the formal Definition identity;
- targetOutputNumbers and prerequisiteWorkItemNumbers are 1-based positions used only to express
your scientific choices; the server creates all formal references.

Scientific rules:
- design research work around the user's actual question using the four-stage framework: problem
modeling, solution design, execution and validation, and insight generation;
- first select suitable workTypeChoice values from availableResearchWorkTypes; then write the plan
from the user's Query. The server keeps only choices that map to registered research work;
- use as many or as few independent work items as the request needs. Do not manufacture a fixed
number of tasks per stage;
- treat goals, questions, outputs, risks, unknowns, constraints, and dependencies as useful planning
context, not a checklist. Omit anything that is not useful;
- preserve explicit user constraints and boundaries without inventing facts, evidence, experiments,
or conclusions;
- this is planning only: do not claim retrieval, analysis, computation, validation, or results have
already happened.
"""
    source_rules = planning_source_prompt_rules(planning_sources)
    if source_rules:
        system_prompt += "\n\n" + source_rules
    payload: dict[str, Any] = {
        "researchRequest": str(raw_input or "").strip(),
        "sourcePage": source_page,
        "selectedHint": selected_hint,
        "researchStageFramework": research_stage_framework(),
        "availableResearchWorkTypes": _work_type_choices(definition_catalog),
        "planningSources": planning_sources_for_provider(planning_sources),
    }
    return system_prompt, json.dumps(payload, ensure_ascii=False, indent=2)


def parse_freeform_scientific_brief(
    payload: Mapping[str, Any],
) -> FreeformProjectScientificBriefV1:
    notes: list[str] = []
    target_outputs, output_numbers = indexed_target_outputs(payload.get("targetOutputs"))
    receive_texts(payload, "targetOutputs", notes)
    raw_items = payload.get("workItems")
    work_items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, Mapping):
                notes.append("规划工作未能映射，原值：" + json.dumps(item, ensure_ascii=False))
                work_items.append({})
                continue
            work_items.append(
                {
                    "scientificObjective": _text(item.get("scientificObjective")),
                    "workTypeChoice": _text(item.get("workTypeChoice")),
                    "targetOutputNumbers": remap_target_numbers(
                        item.get("targetOutputNumbers"),
                        output_numbers,
                        notes,
                        context=f"规划工作 {len(work_items) + 1}",
                    ),
                    "prerequisiteWorkItemNumbers": _numbers(
                        item.get("prerequisiteWorkItemNumbers")
                    ),
                }
            )

    source_uses: list[PlanningSourceUseDecision] = []
    raw_source_uses = payload.get("planningSourceUses")
    if isinstance(raw_source_uses, list):
        for item in raw_source_uses:
            if not isinstance(item, Mapping):
                continue
            try:
                source_uses.append(PlanningSourceUseDecision.model_validate(item))
            except ValidationError:
                continue

    return FreeformProjectScientificBriefV1.model_validate(
        {
            "researchGoal": receive_goal(payload, notes),
            "materialSummary": _text(payload.get("materialSummary")) or None,
            "primaryResearchQuestions": receive_texts(
                payload, "primaryResearchQuestions", notes, "questions"
            ),
            "auxiliaryResearchDimensions": receive_texts(
                payload, "auxiliaryResearchDimensions", notes
            ),
            "targetOutputs": target_outputs,
            "evidenceBoundaries": receive_texts(payload, "evidenceBoundaries", notes),
            "missingInputs": receive_texts(payload, "missingInputs", notes),
            "riskNotes": receive_texts(payload, "riskNotes", notes, "risks")
            + [
                f"待研究未知：{value}"
                for value in receive_texts(payload, "scientificUnknowns", notes, "unknowns")
            ],
            "constraints": receive_texts(payload, "constraints", notes),
            "requestedExternalActions": receive_texts(payload, "requestedExternalActions", notes),
            "workItems": work_items,
            "planningSourceUses": source_uses,
            "contentNotes": notes,
        }
    )


def assemble_freeform_semantic_proposal(
    brief: FreeformProjectScientificBriefV1,
    *,
    definition_catalog: Sequence[Mapping[str, Any]],
    raw_input: str,
) -> PlannerSemanticProposalV2:
    """Keep only registered work and normalize optional model planning hints."""
    choices = _work_type_choices(definition_catalog)
    definition_by_choice = {
        choice["choice"]: definition_catalog[index] for index, choice in enumerate(choices)
    }
    retained: list[tuple[int, FreeformScientificWorkItemV1, Mapping[str, Any]]] = []
    singleton_definitions: set[str] = set()
    for source_index, item in enumerate(brief.work_items, start=1):
        definition = definition_by_choice.get(item.work_type_choice)
        if definition is None:
            brief.content_notes.append(
                "未登记工作未进入执行，原内容："
                + json.dumps(item.model_dump(mode="json", by_alias=True), ensure_ascii=False)
            )
            continue
        definition_id = str(definition.get("definitionId") or "")
        policy = definition.get("instancePolicy")
        mode = str(policy.get("mode") or "") if isinstance(policy, Mapping) else "repeatable"
        if mode == "singleton_collection" and definition_id in singleton_definitions:
            brief.content_notes.append(
                "重复的单实例工作未重复执行，原内容："
                + json.dumps(item.model_dump(mode="json", by_alias=True), ensure_ascii=False)
            )
            continue
        if mode == "singleton_collection":
            singleton_definitions.add(definition_id)
        retained.append((source_index, item, definition))

    if not retained:
        raise PlannerSemanticInputError(
            "Planner response did not select a registered research work",
            error_code="planner_no_registered_work",
            field_paths=("/workItems",),
        )

    target_outputs, output_numbers = indexed_target_outputs(brief.target_outputs)
    target_outputs = target_outputs or [_DEFAULT_TARGET_OUTPUT]
    goal = _text(brief.research_goal) or _text(raw_input) or "当前研究请求"
    local_key_by_source_index = {
        source_index: f"planned_work_{position:02d}"
        for position, (source_index, _item, _definition) in enumerate(retained, start=1)
    }
    work_items: list[dict[str, Any]] = []
    for source_index, item, definition in retained:
        local_key = local_key_by_source_index[source_index]
        objective = _text(item.scientific_objective)
        if not objective:
            objective = _text(definition.get("description")) or _text(definition.get("label"))
        if not objective:
            objective = "完成当前研究所需的已登记研究工作。"
        work_items.append(
            {
                "localKey": local_key,
                "objective": objective,
                "definitionId": str(definition["definitionId"]),
                "inputRefs": [],
                "expectedArtifactTypes": [],
                "targetOutputRefs": [
                    target_outputs[output_numbers[number] - 1]
                    for number in item.target_output_numbers
                    if number in output_numbers
                ],
                "dependsOnLocalKeys": [],
            }
        )

    work_by_source_index = {
        source_index: work_items[position]
        for position, (source_index, _item, _definition) in enumerate(retained)
    }
    dependencies: dict[str, list[str]] = {row["localKey"]: [] for row in work_items}

    def would_cycle(local_key: str, upstream: str) -> bool:
        pending = [upstream]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == local_key:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(dependencies.get(current, ()))
        return False

    for source_index, item, _definition in retained:
        row = work_by_source_index[source_index]
        for upstream_source_index in item.prerequisite_work_item_numbers:
            upstream = local_key_by_source_index.get(upstream_source_index)
            if (
                upstream is None
                or upstream == row["localKey"]
                or upstream in dependencies[row["localKey"]]
            ):
                continue
            if not would_cycle(row["localKey"], upstream):
                dependencies[row["localKey"]].append(upstream)
        row["dependsOnLocalKeys"] = dependencies[row["localKey"]]

    return PlannerSemanticProposalV2.model_validate(
        {
            "schemaVersion": "planner-semantic.v2",
            "goal": goal,
            "understanding": {
                "userIntent": goal,
                "materialSummary": brief.material_summary,
                "primaryResearchQuestions": list(brief.primary_research_questions) or [goal],
                "auxiliaryResearchDimensions": list(brief.auxiliary_research_dimensions),
                "targetOutputs": target_outputs,
                "evidenceBoundaries": list(brief.evidence_boundaries),
                "missingInputs": list(brief.missing_inputs),
                "riskNotes": [*brief.risk_notes, *brief.content_notes],
            },
            "constraints": list(brief.constraints),
            "requestedExternalActions": [],
            "workItems": work_items,
            "targetRequirements": [],
        }
    )


__all__ = [
    "FreeformProjectScientificBriefV1",
    "FreeformScientificWorkItemV1",
    "assemble_freeform_semantic_proposal",
    "freeform_scientific_brief_prompts",
    "freeform_scientific_brief_schema",
    "parse_freeform_scientific_brief",
]

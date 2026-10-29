"""Scientific-semantic enrichment for an already-frozen Workflow plan.

The Provider contract contains only research meaning. Workflow identity, node
selection, dependencies, Definition identity, evaluators, provenance, and other
machine structure remain owned by deterministic code and the compiler.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

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


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _BriefModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="ignore",
        populate_by_name=True,
        strict=False,
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(text for item in value if (text := _text(item))))


def _planning_source_uses(value: object) -> list[PlanningSourceUseDecision]:
    if not isinstance(value, list):
        return []
    selected: list[PlanningSourceUseDecision] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        try:
            decision = PlanningSourceUseDecision.model_validate(item)
        except ValidationError:
            continue
        if decision.source_choice not in seen:
            selected.append(decision)
            seen.add(decision.source_choice)
    return selected


class WorkflowProjectSemanticBriefV2(_BriefModel):
    """Only scientific meaning that cannot be determined from Workflow structure."""

    content_notes: list[str] = []
    research_goal: str = ""
    primary_research_questions: list[str] = []
    auxiliary_research_dimensions: list[str] = []
    target_outputs: list[str] = []
    constraints: list[str] = []
    scientific_unknowns: list[str] = []
    risks_and_tradeoffs: list[str] = []
    work_item_scientific_contexts: list[str] = []
    planning_source_uses: list[PlanningSourceUseDecision] = []


class WorkflowResponsibilityDeltaV4(_BriefModel):
    """Only the project-specific decision for one Provider-local responsibility choice."""

    responsibility_choice: str = ""
    work_item_scientific_contexts: list[str] | None = None
    reuse_candidate_key: str | None = None
    target_output_numbers: list[int] = []


class WorkflowProjectSemanticBriefV4(_BriefModel):
    """Small project semantic delta joined onto a server-owned Workflow skeleton."""

    content_notes: list[str] = []
    research_goal: str = ""
    primary_research_questions: list[str] = []
    auxiliary_research_dimensions: list[str] = []
    target_outputs: list[str] = []
    constraints: list[str] = []
    scientific_unknowns: list[str] = []
    risks_and_tradeoffs: list[str] = []
    responsibility_deltas: list[WorkflowResponsibilityDeltaV4] = []
    planning_source_uses: list[PlanningSourceUseDecision] = []


def workflow_responsibility_choice(position: int) -> str:
    if isinstance(position, bool) or not isinstance(position, int) or position < 1:
        raise ValueError("Workflow responsibility position must be positive")
    return f"responsibility_{position}"


def workflow_semantic_delta_schema(
    *,
    include_planning_sources: bool = False,
    responsibility_choices: Sequence[str],
    core_responsibility_count: int,
    reuse_candidate_keys: Sequence[str] = (),
) -> dict[str, Any]:
    del responsibility_choices, core_responsibility_count, reuse_candidate_keys
    schema = WorkflowProjectSemanticBriefV4.model_json_schema(by_alias=True)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        properties.pop("contentNotes", None)
    if not include_planning_sources:
        if isinstance(properties, dict):
            properties.pop("planningSourceUses", None)
    return schema


def workflow_semantic_delta_prompts(
    *,
    raw_input: str,
    workflow_planning_context: Mapping[str, Any],
    responsibility_descriptions: Sequence[Mapping[str, Any]],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    system_prompt = """You are the scientific planning step in the OpenAIR ResearchDesign Planner.
Return one JSON object containing the useful scientific plan. Use researchGoal (text),
primaryResearchQuestions, auxiliaryResearchDimensions, targetOutputs, constraints,
scientificUnknowns and risksAndTradeoffs (text lists). These are scientific contents,
not a formal system form; omit optional content when it adds no value.
The Workflow already owns registered responsibilities, formal identity, dependencies, provenance,
artifacts, and execution authority. Do not restate or redesign that structure.

Write only the project-specific scientific plan from this user's request: its goal, questions,
useful outputs, explicit constraints, unknowns, risks, and any useful focus for a registered
responsibility. Use responsibilityDeltas for optional entries with responsibilityChoice,
workItemScientificContexts (text list), reuseCandidateKey when reusing, and targetOutputNumbers
(number list). A responsibility delta is optional: omit it when the registered base work already
fits the request. A conditional responsibility is included only when the request or supplied facts
establish that it is needed. For an included delta, copy responsibilityChoice from
researchResponsibilities and use either current-project work context or one supplied reusable
source, never both.
All workItemScientificContexts for a responsibility refine the same research subgoal.
They are focus, scope, or evidence requirements, not separate substage instances.
Do not split one subgoal into collection, screening, recording, or other execution tasks.

For each useful output in targetOutputs, associate its 1-based number with the responsible
research responsibility using targetOutputNumbers. These are scientific associations, not
formal identities. Different outputs may belong to different responsibilities. One output may
require contributions from multiple responsibilities; associate it with every necessary contributor.
Do not assign
source records or evidence assessments to the final recommendation merely because it summarizes
them.

The responsibility marked currentProjectAnswerRequired must remain current-project work. Reuse means
only that the selected prior formal source can satisfy an upstream responsibility after current
applicability checks; never copy or invent formal references. Do not output modes, rationales,
placeholders, Workflow IDs, Definition IDs, dependencies, artifact identities, provenance, budgets,
execution modes, or tool permissions.

Omit information that is not useful. Preserve unknowns as unknowns. Include only explicit user
constraints. Do not claim research or execution has already happened.
"""
    source_rules = planning_source_prompt_rules(planning_sources)
    if source_rules:
        system_prompt += "\n\n" + source_rules
    user_payload = {
        "researchRequest": str(raw_input or "").strip(),
        "workflowPurpose": str(workflow_planning_context.get("description") or "").strip(),
        "researchResponsibilities": [dict(row) for row in responsibility_descriptions],
        "planningSources": planning_sources_for_provider(planning_sources),
    }
    return system_prompt, json.dumps(user_payload, ensure_ascii=False, indent=2)


def parse_workflow_semantic_delta(
    payload: Mapping[str, Any],
) -> WorkflowProjectSemanticBriefV4:
    notes: list[str] = []
    target_outputs, output_numbers = indexed_target_outputs(payload.get("targetOutputs"))
    receive_texts(payload, "targetOutputs", notes)
    deltas = []
    raw_deltas = payload.get("responsibilityDeltas")
    if isinstance(raw_deltas, list):
        for item in raw_deltas:
            if not isinstance(item, Mapping):
                notes.append("职责内容未能映射，原值：" + json.dumps(item, ensure_ascii=False))
                continue
            contexts = receive_texts(
                item, "workItemScientificContexts", notes, "currentProjectWorkContext"
            )
            reuse_key = _text(item.get("reuseCandidateKey")) or None
            deltas.append(
                {
                    "responsibilityChoice": _text(item.get("responsibilityChoice")),
                    "workItemScientificContexts": contexts or None,
                    "reuseCandidateKey": reuse_key,
                    "targetOutputNumbers": remap_target_numbers(
                        item.get("targetOutputNumbers"),
                        output_numbers,
                        notes,
                        context=f"职责 {item.get('responsibilityChoice')}",
                    ),
                }
            )
    return WorkflowProjectSemanticBriefV4.model_validate(
        {
            "researchGoal": receive_goal(payload, notes),
            "primaryResearchQuestions": receive_texts(
                payload, "primaryResearchQuestions", notes, "questions"
            ),
            "auxiliaryResearchDimensions": receive_texts(
                payload, "auxiliaryResearchDimensions", notes
            ),
            "targetOutputs": target_outputs,
            "constraints": receive_texts(payload, "constraints", notes),
            "scientificUnknowns": receive_texts(payload, "scientificUnknowns", notes, "unknowns"),
            "risksAndTradeoffs": receive_texts(payload, "risksAndTradeoffs", notes, "risks"),
            "responsibilityDeltas": deltas,
            "planningSourceUses": _planning_source_uses(payload.get("planningSourceUses")),
            "contentNotes": notes,
        }
    )


def workflow_semantic_brief_schema(
    *,
    include_planning_sources: bool = False,
    work_item_count: int | None = None,
) -> dict[str, Any]:
    """Provider Schema: scientific semantic increment only, with no Workflow IDs."""
    del work_item_count
    schema = WorkflowProjectSemanticBriefV2.model_json_schema(by_alias=True)
    schema["properties"].pop("contentNotes", None)
    if not include_planning_sources:
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("planningSourceUses", None)
    return schema


def workflow_semantic_brief_prompts(
    *,
    raw_input: str,
    workflow_planning_context: Mapping[str, Any],
    work_item_responsibilities: Sequence[str],
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    """Build the minimum scientific input needed for project-specific planning."""
    system_prompt = """You are the scientific-semantic step in the OpenAIR ResearchDesign Planner.
Return one JSON object containing the useful scientific plan. Use researchGoal (text),
primaryResearchQuestions, auxiliaryResearchDimensions, targetOutputs, constraints,
scientificUnknowns and risksAndTradeoffs (text lists). These are scientific contents,
not a formal system form; omit optional content when it adds no value.
The Workflow is already selected. Its registered research work, formal identity, dependencies,
artifacts, evaluators, provenance, budgets, and execution authority are outside your authority.

Write the project-specific plan from this research request: a useful goal, questions, outputs,
explicit constraints, unknowns, risks, and optional scientific focus for registered work. Use only
information that helps answer the user's real question. An entry in workItemScientificContexts is
optional; its position is applied to the matching registered work when present, and missing entries
leave that work's registered base responsibility intact.

Preserve the user's meaning and stated boundaries. Do not invent facts, evidence, experiments,
conclusions, constraints, or execution. Do not design or name Workflow nodes, Definitions,
dependencies, IDs, artifact types, evaluators, provenance, budgets, execution modes, or tool
permissions. Keep unknown facts as unknown.
"""
    source_rules = planning_source_prompt_rules(planning_sources)
    if source_rules:
        system_prompt += "\n\n" + source_rules
    user_payload = {
        "researchRequest": str(raw_input or "").strip(),
        "workflowPurpose": str(workflow_planning_context.get("description") or "").strip(),
        # Responsibilities are scientific work descriptions only. Machine node keys,
        # Definition IDs, dependencies, and artifact identities stay server-owned.
        "researchWorkResponsibilities": [
            str(value).strip() for value in work_item_responsibilities
        ],
        "planningSources": planning_sources_for_provider(planning_sources),
    }
    return system_prompt, json.dumps(user_payload, ensure_ascii=False, indent=2)


def parse_workflow_semantic_brief(payload: Mapping[str, Any]) -> WorkflowProjectSemanticBriefV2:
    notes: list[str] = []
    return WorkflowProjectSemanticBriefV2.model_validate(
        {
            "researchGoal": receive_goal(payload, notes),
            "primaryResearchQuestions": receive_texts(
                payload, "primaryResearchQuestions", notes, "questions"
            ),
            "auxiliaryResearchDimensions": receive_texts(
                payload, "auxiliaryResearchDimensions", notes
            ),
            "targetOutputs": receive_texts(payload, "targetOutputs", notes),
            "constraints": receive_texts(payload, "constraints", notes),
            "scientificUnknowns": receive_texts(payload, "scientificUnknowns", notes, "unknowns"),
            "risksAndTradeoffs": receive_texts(payload, "risksAndTradeoffs", notes, "risks"),
            "workItemScientificContexts": receive_texts(
                payload,
                "workItemScientificContexts",
                notes,
                "currentProjectWorkContext",
                positional=True,
            ),
            "planningSourceUses": _planning_source_uses(payload.get("planningSourceUses")),
            "contentNotes": notes,
        }
    )


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def apply_workflow_semantic_brief(
    semantic: PlannerSemanticProposalV2,
    brief: WorkflowProjectSemanticBriefV2,
) -> PlannerSemanticProposalV2:
    """Deterministically join LLM meaning onto the frozen semantic skeleton."""
    payload = semantic.model_dump(mode="json", by_alias=True)

    # Completion-mode/evaluator semantics are not part of this Provider contract.
    # If the frozen skeleton ever starts owning them, this assembly contract must
    # be revised explicitly rather than guessing how new outputs inherit them.
    if payload.get("targetRequirements"):
        raise ValueError("workflow semantic assembly does not own targetRequirements")

    goal = _text(brief.research_goal) or _text(payload.get("goal"))
    payload["goal"] = goal

    payload["constraints"] = _dedupe([*payload.get("constraints", []), *brief.constraints])
    understanding = payload["understanding"]
    understanding["userIntent"] = goal
    if brief.primary_research_questions:
        understanding["primaryResearchQuestions"] = list(brief.primary_research_questions)
    if brief.auxiliary_research_dimensions:
        understanding["auxiliaryResearchDimensions"] = list(brief.auxiliary_research_dimensions)
    if brief.target_outputs:
        understanding["targetOutputs"] = list(brief.target_outputs)
    # researchType/materialSummary/evidenceBoundaries/missingInputs remain the
    # deterministic skeleton's facts. Scientific unknowns are not user inputs.
    understanding["riskNotes"] = _dedupe(
        [
            *understanding.get("riskNotes", []),
            *brief.content_notes,
            *(f"待研究未知：{value.strip()}" for value in brief.scientific_unknowns),
            *(f"风险与取舍：{value.strip()}" for value in brief.risks_and_tradeoffs),
        ]
    )

    work_items = payload["workItems"]
    contexts = list(brief.work_item_scientific_contexts)
    target_outputs = list(understanding["targetOutputs"])
    for index, row in enumerate(work_items):
        scientific_context = contexts[index] if index < len(contexts) else ""
        base_objective = str(row.get("objective") or "").strip().rstrip("。")
        if scientific_context:
            row["objective"] = (
                f"{base_objective}。当前项目科研重点：{scientific_context}。"
                if base_objective
                else scientific_context
            )
        if row.get("targetOutputRefs"):
            row["targetOutputRefs"] = list(target_outputs)

    return PlannerSemanticProposalV2.model_validate(payload)


__all__ = [
    "WorkflowProjectSemanticBriefV2",
    "WorkflowProjectSemanticBriefV4",
    "WorkflowResponsibilityDeltaV4",
    "apply_workflow_semantic_brief",
    "parse_workflow_semantic_brief",
    "workflow_semantic_brief_prompts",
    "workflow_semantic_brief_schema",
    "workflow_semantic_delta_schema",
    "workflow_semantic_delta_prompts",
    "parse_workflow_semantic_delta",
    "workflow_responsibility_choice",
]

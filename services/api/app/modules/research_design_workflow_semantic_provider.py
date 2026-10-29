"""One bounded scientific-semantic Provider call for a frozen Workflow plan.

A selected Workflow supplies deterministic structure. This adapter performs one
small scientific-semantic call, joins that semantic increment onto the frozen
skeleton, and sends the result through the existing ResearchDesign compiler.
Provider/system failure is not converted into a low-information successful plan.
"""

from __future__ import annotations

from app.execution.research_start_processing import (
    PlannerSemanticInputError,
    local_planning_step,
    read_provider_content,
)

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.execution.llm_structured_runner import (
    _build_responses_payload,
    _extract_responses_output_text,
    _parse_llm_json_content,
    _prepare_payload_for_http,
    extract_final_chat_content,
)
from app.execution.structured_output_provider_adapter import structured_response_format
from app.execution.research_start_provider_adapter import (
    PreparedPlannerAttempt,
    ResearchStartHttpTransport,
    execute_prepared_attempt,
)
from app.modules.research_design_workflow_semantics import (
    apply_workflow_semantic_brief,
    parse_workflow_semantic_delta,
    parse_workflow_semantic_brief,
    workflow_responsibility_choice,
    workflow_semantic_brief_prompts,
    workflow_semantic_brief_schema,
    workflow_semantic_delta_prompts,
    workflow_semantic_delta_schema,
)
from app.modules.research_planning_sources import materialize_planning_source_usages

WORKFLOW_SEMANTIC_MAX_TOKENS = 1400
WORKFLOW_SEMANTIC_DELTA_MAX_TOKENS = 1000


@dataclass(frozen=True)
class WorkflowSemanticProviderConfig:
    provider: str
    model: str
    endpoint: str
    timeout_seconds: float


@dataclass(frozen=True)
class PreparedHybridWorkflowPlannerAttempt:
    """Exactly one scientific-semantic call over a deterministic Workflow skeleton."""

    ordinal: int
    semantic_attempt: PreparedPlannerAttempt

    @property
    def phase(self) -> str:
        return self.semantic_attempt.phase

    @property
    def provider(self) -> str:
        return self.semantic_attempt.provider

    @property
    def model(self) -> str:
        return self.semantic_attempt.model

    @property
    def endpoint(self) -> str:
        return self.semantic_attempt.endpoint

    @property
    def timeout_seconds(self) -> float:
        return self.semantic_attempt.timeout_seconds

    @property
    def payload(self) -> dict[str, Any]:
        return self.semantic_attempt.payload

    @property
    def response_format(self) -> dict[str, Any] | None:
        return self.semantic_attempt.response_format

    @property
    def provider_schema(self) -> dict[str, Any] | None:
        return self.semantic_attempt.provider_schema

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_attempt.to_dict(), "mode": "workflow_scientific_semantics"}

    def execute(self, *, transport: ResearchStartHttpTransport | None = None) -> Any:
        # Existing provider lifecycle owns timeout/transport/contract classification.
        # Do not turn a system failure into a generic confirmable ResearchDesign.
        return execute_prepared_attempt(self.semantic_attempt, transport=transport)


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = _parse_llm_json_content(content)
    except (ValueError, TypeError) as exc:
        error = PlannerSemanticInputError(
            "Workflow scientific response is not a JSON object",
            error_code="planner_response_json_invalid", field_paths=("/",),
        )
        error.processing_stage = "response_json"
        raise error from exc
    if not isinstance(parsed, Mapping):
        raise PlannerSemanticInputError(
            "Workflow scientific response must be an object",
            error_code="planner_response_json_invalid", field_paths=("/",),
        )
    return dict(parsed)


def _adaptive_responsibility_descriptions(
    *,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    catalog = {
        (str(row.get("definitionId") or ""), int(row.get("definitionVersion") or 0)): row
        for row in definition_catalog
        if str(row.get("definitionId") or "")
    }
    rows: list[tuple[Mapping[str, Any], str]] = []
    for requirement, field_name in (("core", "coreResponsibilities"), ("conditional", "conditionalResponsibilities")):
        values = workflow_planning_context.get(field_name)
        if not isinstance(values, list):
            raise ValueError("adaptive Workflow responsibility catalog is invalid")
        rows.extend((row, requirement) for row in values if isinstance(row, Mapping))
    rows.sort(key=lambda pair: int(pair[0].get("displayOrder") or 0))

    final_core_position = max(
        index for index, (_row, requirement) in enumerate(rows, start=1)
        if requirement == "core"
    )
    descriptions: list[dict[str, Any]] = []
    for position, (row, requirement) in enumerate(rows, start=1):
        definition_id = str(row.get("definitionId") or "")
        version = int(row.get("definitionVersion") or 0)
        definition = catalog.get((definition_id, version))
        if definition is None:
            raise ValueError("adaptive Workflow responsibility Definition cannot be resolved")
        description = str(definition.get("description") or definition.get("label") or "").strip()
        if not description:
            raise ValueError("adaptive Workflow responsibility requires a scientific description")
        descriptions.append({
            "responsibilityChoice": workflow_responsibility_choice(position),
            "requirement": requirement,
            "responsibility": description,
            "currentProjectAnswerRequired": position == final_core_position,
        })
    return descriptions


def prepare_hybrid_workflow_planner_attempt(
    *,
    raw_input: str,
    ordinal: int,
    definition_catalog: Sequence[Mapping[str, Any]],
    workflow_planning_context: Mapping[str, Any],
    provider_config: WorkflowSemanticProviderConfig,
    planning_sources: Sequence[Mapping[str, Any]] = (),
) -> PreparedHybridWorkflowPlannerAttempt:
    """Prepare exactly one small semantic call followed by deterministic compile."""
    from app.modules.research_design_compiler import compile_research_design_proposal
    from app.modules.research_design_planner_assembly import (
        build_adaptive_workflow_semantics,
        build_deterministic_workflow_semantics,
    )
    from app.modules.research_design_service import PlannerProviderResult

    context_schema = workflow_planning_context.get("schemaVersion")
    if context_schema == "workflow-planning-context.v1":
        base_semantic = build_deterministic_workflow_semantics(
            raw_input=raw_input,
            definition_catalog=definition_catalog,
            workflow_planning_context=workflow_planning_context,
        )
        work_item_responsibilities = [row.objective for row in base_semantic.work_items]
        output_schema = workflow_semantic_brief_schema(
            include_planning_sources=bool(planning_sources),
            work_item_count=len(work_item_responsibilities),
        )
        system_prompt, user_prompt = workflow_semantic_brief_prompts(
            raw_input=raw_input,
            workflow_planning_context=workflow_planning_context,
            work_item_responsibilities=work_item_responsibilities,
            planning_sources=planning_sources,
        )
        schema_name = "workflow_project_semantic_brief_v2"
        max_tokens = WORKFLOW_SEMANTIC_MAX_TOKENS


    elif context_schema == "workflow-planning-context.v2":
        responsibility_descriptions = _adaptive_responsibility_descriptions(
            definition_catalog=definition_catalog,
            workflow_planning_context=workflow_planning_context,
        )
        responsibility_choices = [
            str(row["responsibilityChoice"])
            for row in responsibility_descriptions
        ]
        core_responsibility_count = sum(
            row["requirement"] == "core" for row in responsibility_descriptions
        )
        reuse_candidate_keys = [
            str(row.get("sourceChoice") or "").strip()
            for row in planning_sources
        ]
        if any(not value for value in reuse_candidate_keys) or len(
            set(reuse_candidate_keys)
        ) != len(reuse_candidate_keys):
            raise ValueError("planning source context contains invalid choices")
        output_schema = workflow_semantic_delta_schema(
            include_planning_sources=bool(planning_sources),
            responsibility_choices=responsibility_choices,
            core_responsibility_count=core_responsibility_count,
            reuse_candidate_keys=reuse_candidate_keys,
        )
        system_prompt, user_prompt = workflow_semantic_delta_prompts(
            raw_input=raw_input,
            workflow_planning_context=workflow_planning_context,
            responsibility_descriptions=responsibility_descriptions,
            planning_sources=planning_sources,
        )
        schema_name = "workflow_project_semantic_delta_v4"
        max_tokens = WORKFLOW_SEMANTIC_DELTA_MAX_TOKENS


    else:
        raise ValueError("unsupported frozen Workflow planning context")

    def decode_semantic(parsed: Mapping[str, Any], body: Mapping[str, Any]) -> PlannerProviderResult:
        call_ref = body.get("id")
        if not isinstance(call_ref, str) or not call_ref.strip():
            error = PlannerSemanticInputError(
                "Workflow response has no Provider call identity",
                error_code="provider_call_identity_missing", field_paths=("/id",),
            )
            error.processing_stage = "provider_envelope"
            raise error
        if context_schema == "workflow-planning-context.v1":
            brief = parse_workflow_semantic_brief(parsed)
        else:
            brief = parse_workflow_semantic_delta(parsed)
        with local_planning_step("workflow_assembly"):
            if context_schema == "workflow-planning-context.v1":
                semantic = apply_workflow_semantic_brief(base_semantic, brief)
            else:
                semantic = build_adaptive_workflow_semantics(
                    raw_input=raw_input, definition_catalog=definition_catalog,
                    workflow_planning_context=workflow_planning_context, brief=brief,
                    planning_sources=planning_sources,
                )
            source_usages = materialize_planning_source_usages(brief.planning_source_uses, planning_sources)
        with local_planning_step("research_design_compile"):
            proposal = compile_research_design_proposal(
                semantic, definition_catalog=definition_catalog,
                raw_input=raw_input, workflow_planning_context=workflow_planning_context,
            )
            return PlannerProviderResult(
                proposal=proposal.model_dump(mode="json", by_alias=True),
                provider_call_ref=call_ref.strip(), planning_source_usages=source_usages,
            )

    config = provider_config
    if config.provider == "huiyan_chat_completions":
        response_format = structured_response_format(
            config.provider,
            schema_name=schema_name,
        )
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
            "reasoning_effort": "none",
            "response_format": response_format,
        }
        payload, _ = _prepare_payload_for_http(payload)

        def decode(body: Mapping[str, Any]) -> PlannerProviderResult:
            content, _choice = read_provider_content(extract_final_chat_content, body)
            return decode_semantic(_parse_json_object(content), body)

    elif config.provider == "openai_responses":
        response_format = structured_response_format(
            config.provider,
            schema_name=schema_name,
        )
        payload = _build_responses_payload(
            model=config.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
            schema_name=schema_name,
            max_completion_tokens=max_tokens,
            reasoning_effort="none",
            temperature=0.1,
        )
        payload, _ = _prepare_payload_for_http(payload)

        def decode(body: Mapping[str, Any]) -> PlannerProviderResult:
            return decode_semantic(
                _parse_json_object(read_provider_content(_extract_responses_output_text, body)),
                body,
            )

    else:
        raise ValueError(f"unsupported Workflow semantic provider: {config.provider}")

    semantic_attempt = PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=ordinal,
        provider=config.provider,
        model=config.model,
        endpoint=config.endpoint,
        payload=payload,
        response_format=response_format,
        timeout_seconds=max(float(config.timeout_seconds), 0.1),
        decode=decode,
        provider_schema=output_schema,
    )
    return PreparedHybridWorkflowPlannerAttempt(
        ordinal=ordinal,
        semantic_attempt=semantic_attempt,
    )


__all__ = [
    "PreparedHybridWorkflowPlannerAttempt",
    "WorkflowSemanticProviderConfig",
    "prepare_hybrid_workflow_planner_attempt",
]

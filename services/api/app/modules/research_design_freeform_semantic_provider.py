"""One scientific-semantic Provider call for freeform ResearchDesign planning.

This module is the Provider boundary for Research Start requests that do not use
a frozen Workflow. The LLM returns only scientific meaning plus provider-local
choices. Formal identities and references are restored by deterministic code and
then checked by the existing ResearchDesign compiler.
"""

from __future__ import annotations

from app.execution.research_start_processing import (
    PlannerSemanticInputError,
    local_planning_step,
    read_provider_content,
)

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.execution.llm_structured_runner import (
    _build_responses_payload,
    _extract_responses_output_text,
    _prepare_payload_for_http,
    apply_openai_compatible_thinking,
    extract_final_chat_content,
)
from app.execution.structured_output_provider_adapter import structured_response_format
from app.execution.research_start_provider_adapter import (
    PreparedPlannerAttempt,
)
from app.modules.research_design_freeform_semantics import (
    assemble_freeform_semantic_proposal,
    freeform_scientific_brief_prompts,
    freeform_scientific_brief_schema,
    parse_freeform_scientific_brief,
)
from app.modules.research_planning_sources import materialize_planning_source_usages


@dataclass(frozen=True)
class FreeformSemanticProviderConfig:
    provider: str
    model: str
    endpoint: str
    timeout_seconds: float
    max_tokens: int
    temperature: float


def _json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise PlannerSemanticInputError(
            "Planner response is not valid JSON",
            error_code="planner_response_json_invalid",
            field_paths=("/",),
        ) from exc
    if not isinstance(parsed, Mapping):
        raise PlannerSemanticInputError(
            "Planner response must be a JSON object",
            error_code="planner_response_json_invalid",
            field_paths=("/",),
        )
    return dict(parsed)


def _compile_brief(
    payload: Mapping[str, Any],
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
    response_body: Mapping[str, Any],
    planning_sources: Sequence[Mapping[str, Any]] = (),
):
    from app.modules.research_design_compiler import compile_research_design_proposal
    from app.modules.research_design_service import PlannerProviderResult

    brief = parse_freeform_scientific_brief(payload)
    semantic = assemble_freeform_semantic_proposal(
        brief,
        definition_catalog=definition_catalog,
        raw_input=raw_input,
    )
    proposal = compile_research_design_proposal(
        semantic,
        definition_catalog=definition_catalog,
        raw_input=raw_input,
        workflow_planning_context=None,
    )

    call_ref = response_body.get("id")
    if not isinstance(call_ref, str) or not call_ref.strip():
        raise PlannerSemanticInputError(
            "Planner response has no Provider call identity",
            error_code="provider_call_identity_missing", field_paths=("/id",),
        )
    return PlannerProviderResult(
        proposal=proposal.model_dump(mode="json", by_alias=True),
        provider_call_ref=call_ref.strip(),
        planning_source_usages=materialize_planning_source_usages(
            brief.planning_source_uses, planning_sources
        ),
    )


def prepare_freeform_research_design_attempt(
    *,
    raw_input: str,
    source_page: str,
    selected_hint: str | None,
    ordinal: int,
    definition_catalog: Sequence[Mapping[str, Any]],
    provider_config: FreeformSemanticProviderConfig,
    planning_sources: Sequence[Mapping[str, Any]] = (),
    prior_failure: Any = None,
) -> PreparedPlannerAttempt:
    del prior_failure

    output_schema = freeform_scientific_brief_schema(
        definition_catalog,
        include_planning_sources=bool(planning_sources),
    )
    system_prompt, user_prompt = freeform_scientific_brief_prompts(
        raw_input=raw_input,
        source_page=source_page,
        selected_hint=selected_hint,
        definition_catalog=definition_catalog,
        planning_sources=planning_sources,
    )
    config = provider_config
    response_format = structured_response_format(
        config.provider,
        schema_name="freeform_project_scientific_brief_v1",
    )

    if config.provider == "huiyan_chat_completions":
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "response_format": response_format,
        }
        payload = apply_openai_compatible_thinking(
            payload,
            "OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING",
        )
        payload, _ = _prepare_payload_for_http(payload)

        def decode(body: Mapping[str, Any]):
            content, _choice = read_provider_content(extract_final_chat_content, body)
            with local_planning_step("freeform_assembly_compile"):
                return _compile_brief(
                    _json_object(content),
                    raw_input=raw_input,
                    definition_catalog=definition_catalog,
                    response_body=body,
                    planning_sources=planning_sources,
                )

    elif config.provider == "openai_responses":
        payload = _build_responses_payload(
            model=config.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
            schema_name="freeform_project_scientific_brief_v1",
            max_completion_tokens=config.max_tokens,
            reasoning_effort=None,
            temperature=config.temperature,
        )
        payload, _ = _prepare_payload_for_http(payload)

        def decode(body: Mapping[str, Any]):
            with local_planning_step("freeform_assembly_compile"):
                return _compile_brief(
                    _json_object(read_provider_content(_extract_responses_output_text, body)),
                    raw_input=raw_input,
                    definition_catalog=definition_catalog,
                    response_body=body,
                    planning_sources=planning_sources,
                )

    else:
        raise ValueError(f"unsupported freeform Planner provider: {config.provider}")

    return PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=ordinal,
        provider=config.provider,
        model=config.model,
        endpoint=config.endpoint,
        payload=payload,
        response_format=response_format,
        timeout_seconds=config.timeout_seconds,
        decode=decode,
        provider_schema=output_schema,
    )


__all__ = [
    "FreeformSemanticProviderConfig",
    "prepare_freeform_research_design_attempt",
]

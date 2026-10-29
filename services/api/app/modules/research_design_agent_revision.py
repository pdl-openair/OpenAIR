"""Targeted natural-language revision authority for an unconfirmed ResearchDesign.

The model is allowed to propose only user-facing semantic edits: the research
objective and the objective text of already-persisted SubstageInstances.  All
canonical structural authority (Definition identity, stage/substage order,
dependency graph, artifact/input contracts, acceptance semantics and frozen
registry snapshot) is reconstructed from the current ResearchDesign and frozen
Workflow/compiler for historical missing ownership. Structural authority is not
present in the model output contract.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.modules.research_design_service import (
    PlannerProviderResult,
    ResearchDesignProposal,
    ResearchDesignProposalError,
    ResearchDesignRevisionSaveUnavailable,
    ResearchDesignRevisionUserError,
    ResearchDesignService,
    classify_research_design_revision_failure,
    validate_research_design_proposal,
)
from app.modules.research_orchestration_repository import TaskRevisionConflictError
from app.modules.research_start_planner_budget import research_start_planner_task_budget_seconds
from app.modules.target_output_ownership import (
    legacy_revision_context,
    restore_legacy_revision_targets,
    revision_target_metadata,
)
from app.modules.target_output_ownership import (
    revision_coverage_maps as _coverage_maps,
)

class _AgentPatchWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    substageInstanceId: str = Field(min_length=1)
    objective: str = Field(min_length=1)

    @field_validator("substageInstanceId", "objective")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class _AgentResearchDesignPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    workItems: list[_AgentPatchWorkItem] = Field(min_length=1, max_length=64)

    @field_validator("goal")
    @classmethod
    def strip_goal(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


ModelPatchProvider = Callable[[str, Any], tuple[_AgentResearchDesignPatch, str]]


def _identity_error(message: str) -> ResearchDesignRevisionUserError:
    return ResearchDesignRevisionUserError(
        "这次修改会改变研究方案的固定结构，已停止应用。请只调整研究目标或现有研究工作的内容。"
    )


def _validate_targeted_patch(payload: Mapping[str, Any], latest_design: Any) -> _AgentResearchDesignPatch:
    try:
        patch = _AgentResearchDesignPatch.model_validate(payload)
    except ValidationError as exc:
        raise _identity_error("agent revision payload is invalid") from exc
    expected_ids = [row.substage_instance_id for row in latest_design.instances]
    actual_ids = [row.substageInstanceId for row in patch.workItems]
    if actual_ids != expected_ids:
        raise _identity_error("agent revision attempted to add, remove, or reorder substages")
    return patch


def _proposal_from_current_design(
    latest_design: Any,
    patch: _AgentResearchDesignPatch,
    *,
    workflow_admission: Mapping[str, Any] | None = None,
    definition_source: Any = None,
    query_owner_provider: Callable[[Mapping[str, Any]], Mapping[str, str]] | None = None,
) -> ResearchDesignProposal:
    source_path_by_target_id, instance_key_by_id, criterion_owner = _coverage_maps(latest_design)
    objective_by_id = {row.substageInstanceId: row.objective for row in patch.workItems}

    instances: list[dict[str, Any]] = []
    for instance in latest_design.instances:
        instance_key = instance_key_by_id[instance.substage_instance_id]
        criteria: list[dict[str, Any]] = []
        for index, criterion in enumerate(instance.acceptance_criteria):
            try:
                coverage_paths = [source_path_by_target_id[target_id] for target_id in criterion.coverage_target_ids]
            except KeyError as exc:
                raise ResearchDesignRevisionSaveUnavailable(
                    "当前研究方案的验收记录不完整，请重新加载后再试。"
                ) from exc
            criteria.append({
                "criterionKey": f"preserved_criterion_{index}",
                "description": criterion.description,
                "criterionKind": criterion.criterion_kind.value,
                "coverageSourcePaths": coverage_paths,
                "requiredArtifactType": criterion.required_artifact_type,
                "judgmentRequirement": criterion.judgment_requirement.value,
                "evaluatorType": criterion.evaluator_type.value,
                "evaluatorRef": criterion.evaluator_ref,
            })

        dependencies: list[dict[str, Any]] = []
        for index, dependency in enumerate(instance.dependencies):
            upstream_id = dependency.upstream_instance_ref.substage_instance_id
            upstream_key = instance_key_by_id.get(upstream_id)
            if upstream_key is None:
                raise ResearchDesignRevisionSaveUnavailable(
                    "当前研究方案的依赖关系不完整，请重新加载后再试。"
                )
            dependencies.append({
                "dependencyKey": f"preserved_dependency_{index}",
                "upstreamInstanceKey": upstream_key,
                "conditionType": dependency.condition_type.value,
                "requiredArtifactTypes": list(dependency.required_artifact_types),
                "confirmationTargetRef": dependency.confirmation_target_ref,
            })

        instances.append({
            "instanceKey": instance_key,
            "definitionId": instance.substage_definition_id,
            "objective": objective_by_id[instance.substage_instance_id],
            "inputRefs": list(instance.input_refs),
            "expectedArtifactTypes": list(instance.expected_artifact_types),
            "acceptanceCriteria": criteria,
            "dependencies": dependencies,
            "sequenceHint": instance.sequence_hint,
        })

    coverage = latest_design.coverage_report
    assert coverage is not None
    candidates: list[dict[str, Any]] = []
    for target in coverage.targets:
        claim = next((row for row in coverage.claims if row.target_id == target.target_id), None)
        if claim is None:
            raise ResearchDesignRevisionSaveUnavailable(
                "当前研究方案的覆盖记录不完整，请重新加载后再试。"
            )
        try:
            instance_keys = [
                instance_key_by_id[ref.substage_instance_id]
                for ref in claim.instance_refs
            ]
            criterion_keys = [criterion_owner[ref] for ref in claim.acceptance_criterion_refs]
        except KeyError as exc:
            raise ResearchDesignRevisionSaveUnavailable(
                "当前研究方案的覆盖引用已失效，请重新加载后再试。"
            ) from exc
        candidates.append({
            "sourcePath": target.source_path,
            "instanceKeys": instance_keys,
            "acceptanceCriterionKeys": criterion_keys,
            "rationale": claim.rationale,
        })

    payload = {
        "planningMode": latest_design.planning_mode.value,
        "planningComplexity": latest_design.planning_complexity.model_dump(mode="json", by_alias=True),
        "goal": patch.goal,
        "understanding": latest_design.understanding.model_dump(mode="json", by_alias=True),
        "constraints": list(latest_design.constraints),
        "scopeBoundary": latest_design.scope_boundary.model_dump(mode="json", by_alias=True),
        "budgetBoundary": latest_design.budget_boundary.model_dump(mode="json", by_alias=True),
        "externalActionBoundary": latest_design.external_action_boundary.model_dump(mode="json", by_alias=True),
        "riskBoundary": latest_design.risk_boundary.model_dump(mode="json", by_alias=True),
        "instances": instances,
        "coverageClaimCandidates": candidates,
        **revision_target_metadata(latest_design, instance_key_by_id),
    }
    context = legacy_revision_context(latest_design, workflow_admission)
    if context is not None:
        if definition_source is None or query_owner_provider is None:
            raise ResearchDesignRevisionSaveUnavailable("历史方案修订服务暂不可用，原方案已保留。")
        restore_legacy_revision_targets(
            payload,
            context=context,
            definition_catalog=definition_source.planner_catalog_for_research_design(
                workflow_admission
            ),
            query_owner_provider=query_owner_provider,
        )
    try:
        return validate_research_design_proposal(payload)
    except ResearchDesignProposalError as exc:
        raise classify_research_design_revision_failure(exc) from exc


def _assert_frozen_identity(latest_design: Any, proposal: ResearchDesignProposal, definition_source: Any) -> None:
    if len(proposal.instances) != len(latest_design.instances):
        raise _identity_error("agent revision changed substage cardinality")
    for current, revised in zip(latest_design.instances, proposal.instances, strict=True):
        expected_key = f"existing_instance__{current.substage_instance_id}"
        if (
            revised.instance_key != expected_key
            or revised.definition_id != current.substage_definition_id
            or revised.sequence_hint != current.sequence_hint
            or revised.input_refs != list(current.input_refs)
            or revised.expected_artifact_types != list(current.expected_artifact_types)
        ):
            raise _identity_error("agent revision changed frozen substage authority")
        definition = definition_source.resolve(revised.definition_id)
        if (
            definition is None
            or definition.definition_id != current.substage_definition_id
            or definition.definition_version != current.substage_definition_version
            or str(definition.stage_key) != str(current.stage_key)
        ):
            raise ResearchDesignRevisionSaveUnavailable(
                "当前研究方案引用的研究步骤定义已变化，请重新加载后再试。"
            )
        expected_dependencies = [
            (
                row.upstream_instance_ref.substage_instance_id,
                row.condition_type.value,
                tuple(row.required_artifact_types),
                row.confirmation_target_ref,
            )
            for row in current.dependencies
        ]
        actual_dependencies = [
            (
                row.upstream_instance_key.removeprefix("existing_instance__"),
                row.condition_type,
                tuple(row.required_artifact_types),
                row.confirmation_target_ref,
            )
            for row in revised.dependencies
        ]
        if actual_dependencies != expected_dependencies:
            raise _identity_error("agent revision changed the dependency graph")


def _patch_prompt(instruction: str, latest_design: Any) -> tuple[str, str, dict[str, Any]]:
    output_schema = _AgentResearchDesignPatch.model_json_schema()
    current = {
        "goal": latest_design.goal,
        "workItems": [
            {
                "substageInstanceId": row.substage_instance_id,
                "stageKey": str(row.stage_key),
                "objective": row.objective,
            }
            for row in latest_design.instances
        ],
    }
    system_prompt = (
        "You revise an unconfirmed scientific ResearchDesign. Return only a targeted semantic patch. "
        "You may edit only the research goal and objective text of existing work items. Never add, remove, "
        "rename, reorder, merge, or split work items. Never change stage identity, Definition identity, "
        "dependencies, artifacts, skills, execution authority, or workflow identity. Preserve every unaffected "
        "goal/objective verbatim. Do not execute research and do not return commentary."
    )
    user_prompt = json.dumps(
        {
            "instruction": instruction,
            "currentResearchDesign": current,
        },
        ensure_ascii=False,
        indent=2,
    )
    return system_prompt, user_prompt, output_schema


def _json_object(content: str) -> Mapping[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("agent revision provider returned a non-object JSON value")
    return parsed


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            return max(minimum, min(int(raw), maximum))
        except ValueError:
            pass
    return default


def _bounded_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw:
        try:
            return max(minimum, min(float(raw), maximum))
        except ValueError:
            pass
    return default


def _agent_revision_provider() -> str:
    return (
        os.environ.get("OPENAIR_RESEARCH_START_PLANNER_PROVIDER")
        or os.environ.get("OPENTRAJECTORY_LLM_PROVIDER")
        or "huiyan_chat_completions"
    ).strip().lower()


def _agent_revision_chat_model() -> str:
    return (
        os.environ.get("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL")
        or os.environ.get("LLM_PROVIDER_MODEL_SOL")
        or os.environ.get("LLM_PROVIDER_MODEL")
        or os.environ.get("OPENTRAJECTORY_LIVE_LLM_MODEL")
        or "gpt-4o-mini"
    ).strip()


def _agent_revision_openai_model() -> str:
    return (
        os.environ.get("OPENAIR_RESEARCH_START_PLANNER_LLM_MODEL")
        or os.environ.get("LLM_PROVIDER_MODEL_SOL")
        or os.environ.get("OPENTRAJECTORY_LIVE_LLM_MODEL")
        or "gpt-5-mini"
    ).strip()


def _agent_revision_max_tokens() -> int:
    return _bounded_int_env("OPENAIR_RESEARCH_START_PLANNER_LLM_MAX_TOKENS", 1800, 600, 5000)


def _agent_revision_temperature() -> float:
    return _bounded_float_env("OPENAIR_RESEARCH_START_PLANNER_LLM_TEMPERATURE", 0.2, 0.0, 1.0)


def _agent_revision_response_format_enabled() -> bool:
    raw = (os.environ.get("OPENAIR_RESEARCH_START_PLANNER_LLM_RESPONSE_FORMAT") or "auto").strip().lower()
    return raw not in {"0", "false", "off", "disabled", "none"}


def _invoke_targeted_model_patch(instruction: str, latest_design: Any) -> tuple[_AgentResearchDesignPatch, str]:
    # Reuse the same low-level provider transport/config primitives as Research Start,
    # without entering the research-start Planner path.
    from app.execution.llm_structured_runner import (
        _build_responses_payload,
        _extract_responses_output_text,
        _prepare_payload_for_http,
        apply_openai_compatible_thinking,
        extract_final_chat_content,
    )
    from app.execution.research_start_provider_adapter import PreparedPlannerAttempt, execute_prepared_attempt
    from app.execution.structured_output_provider_adapter import structured_response_format

    system_prompt, user_prompt, output_schema = _patch_prompt(instruction, latest_design)
    provider = _agent_revision_provider()
    if provider == "huiyan_chat_completions":
        api_key = os.environ.get("LLM_PROVIDER_API_KEY")
        api_base = os.environ.get("LLM_PROVIDER_BASE_URL")
        if not api_key or not api_base:
            raise ResearchDesignRevisionSaveUnavailable("本次研究方案修改服务暂不可用，请重试。")
        model = _agent_revision_chat_model()
        response_format = structured_response_format(
            provider,
            schema_name="research_design_targeted_revision_v1",
            enabled=_agent_revision_response_format_enabled(),
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": _agent_revision_temperature(),
            "max_tokens": min(_agent_revision_max_tokens(), 2200),
        }
        if response_format is not None:
            payload["response_format"] = response_format
        payload = apply_openai_compatible_thinking(payload, "OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING")
        endpoint = f"{api_base.rstrip('/')}/v1/chat/completions"

        def decode(body: Mapping[str, Any]) -> tuple[_AgentResearchDesignPatch, str]:
            content, _choice = extract_final_chat_content(dict(body))
            provider_ref = body.get("id")
            if not isinstance(provider_ref, str) or not provider_ref.strip():
                raise ValueError("agent revision provider response is missing a call identity")
            return _validate_targeted_patch(_json_object(content), latest_design), provider_ref.strip()

    elif provider == "openai_responses":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ResearchDesignRevisionSaveUnavailable("本次研究方案修改服务暂不可用，请重试。")
        model = _agent_revision_openai_model()
        response_format = structured_response_format(
            provider,
            schema_name="research_design_targeted_revision_v1",
        )
        payload = _build_responses_payload(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
            schema_name="research_design_targeted_revision_v1",
            max_completion_tokens=min(_agent_revision_max_tokens(), 2200),
            reasoning_effort=os.environ.get("OPENAIR_RESEARCH_START_PLANNER_REASONING_EFFORT") or None,
            temperature=_agent_revision_temperature(),
        )
        endpoint = f"{os.environ.get('OPENAI_API_BASE_URL', 'https://api.openai.com').rstrip('/')}/v1/responses"

        def decode(body: Mapping[str, Any]) -> tuple[_AgentResearchDesignPatch, str]:
            provider_ref = body.get("id")
            if not isinstance(provider_ref, str) or not provider_ref.strip():
                raise ValueError("agent revision provider response is missing a call identity")
            content = _extract_responses_output_text(dict(body))
            return _validate_targeted_patch(_json_object(content), latest_design), provider_ref.strip()

    else:
        raise ResearchDesignRevisionSaveUnavailable("本次研究方案修改服务暂不可用，请重试。")

    payload, _ = _prepare_payload_for_http(payload)
    prepared = PreparedPlannerAttempt(
        phase="research_design_agent_revision",
        ordinal=1,
        provider=provider,
        model=model,
        endpoint=endpoint,
        payload=payload,
        response_format=response_format,
        timeout_seconds=research_start_planner_task_budget_seconds(),
        decode=decode,
        provider_schema=output_schema,
    )
    return execute_prepared_attempt(prepared)



def _legacy_query_associations(
    latest_design: Any,
    patch: _AgentResearchDesignPatch,
    context: Mapping[str, Any],
    definition_source: Any,
    admission: Mapping[str, Any] | None,
) -> tuple[dict[str, str], str]:
    """Use the existing scientific Workflow planner only for unrecorded Query ownership.

    Its graph is not applied: the targeted revision still owns identity/dependencies.
    Only validated scientific target associations feed the deterministic compiler.
    """
    from app.modules.research_design_workflow_semantic_provider import (
        WorkflowSemanticProviderConfig,
        prepare_hybrid_workflow_planner_attempt,
    )

    provider = _agent_revision_provider()
    model = (
        _agent_revision_chat_model()
        if provider == "huiyan_chat_completions"
        else _agent_revision_openai_model()
    )
    base = os.environ.get("LLM_PROVIDER_BASE_URL", "")
    endpoint = (
        f"{base.rstrip('/')}/v1/chat/completions"
        if provider == "huiyan_chat_completions"
        else f"{os.environ.get('OPENAI_API_BASE_URL', 'https://api.openai.com').rstrip('/')}/v1/responses"
    )
    raw_input = json.dumps(
        {
            "researchGoal": patch.goal,
            "existingUnderstanding": latest_design.understanding.model_dump(
                mode="json", by_alias=True
            ),
            "existingWork": [row.objective for row in patch.workItems],
            "instruction": "Preserve every existing target verbatim. "
            "Supply its scientific responsibility association."
            " Do not treat the historical terminal-work mapping as evidence of ownership.",
        },
        ensure_ascii=False,
    )
    prepared = prepare_hybrid_workflow_planner_attempt(
        raw_input=raw_input,
        ordinal=1,
        definition_catalog=definition_source.planner_catalog_for_research_design(admission),
        workflow_planning_context=context,
        provider_config=WorkflowSemanticProviderConfig(
            provider=provider,
            model=model,
            endpoint=endpoint,
            timeout_seconds=research_start_planner_task_budget_seconds(),
        ),
    )
    result = prepared.execute()
    proposal = validate_research_design_proposal(result.proposal)
    owners = {
        output: proposal.target_output_owners[f"understanding.targetOutputs[{index}]"]
        for index, output in enumerate(proposal.understanding.target_outputs)
    }
    return owners, result.provider_call_ref


def revise_research_design_with_agent(
    *,
    task_id: str,
    owner_id: str,
    workspace_id: str,
    instruction: str,
    expected_task_revision: int,
    request_id: str | None = None,
    repository: Any | None = None,
    definition_source: Any | None = None,
    model_patch_provider: ModelPatchProvider | None = None,
) -> Any:
    """Apply one natural-language instruction through canonical ResearchDesign.revise."""
    if repository is None:
        from app.modules.research_orchestration_repository import ResearchOrchestrationRepository

        repository = ResearchOrchestrationRepository()
    aggregate = repository.load(task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
    if aggregate.revision != expected_task_revision:
        raise TaskRevisionConflictError("task_revision_conflict")
    if not aggregate.research_design_versions:
        raise ResearchDesignRevisionUserError("当前任务没有可修改的研究方案。")
    latest_design = max(aggregate.research_design_versions, key=lambda row: row.version)

    if definition_source is None:
        from app.modules.production_definition_source import ProductionDefinitionSource

        definition_source = ProductionDefinitionSource(
            snapshot_ref=aggregate.registry_snapshot_ref.model_dump(mode="json", by_alias=True),
        )
    service = ResearchDesignService(repository=repository, definition_source=definition_source)
    patch_provider = model_patch_provider or _invoke_targeted_model_patch
    raw_instruction = instruction
    instruction = instruction.strip()
    if not instruction:
        raise ResearchDesignRevisionUserError("请输入希望如何修改研究方案。")
    instruction_digest = hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16]

    def provider(_idempotency_key: str) -> PlannerProviderResult:
        try:
            patch, provider_call_ref = patch_provider(instruction, latest_design)
            patch = _validate_targeted_patch(
                patch.model_dump(mode="json") if isinstance(patch, _AgentResearchDesignPatch) else patch,
                latest_design,
            )
            ownership_call_refs: list[str] = []

            def query_owners(context: Mapping[str, Any]) -> Mapping[str, str]:
                result, call_ref = _legacy_query_associations(
                    latest_design,
                    patch,
                    context,
                    definition_source,
                    workflow_admission,
                )
                ownership_call_refs.append(call_ref)
                return result

            workflow_admission = repository.load_planning_workflow_admission(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            proposal = _proposal_from_current_design(
                latest_design,
                patch,
                workflow_admission=workflow_admission,
                definition_source=definition_source,
                query_owner_provider=query_owners,
            )
            _assert_frozen_identity(latest_design, proposal, definition_source)
            return PlannerProviderResult(
                proposal=proposal.model_dump(mode="json", by_alias=True),
                provider_call_ref=(
                    ownership_call_refs[-1] if ownership_call_refs else provider_call_ref
                ),
            )
        except (ResearchDesignRevisionUserError, ResearchDesignRevisionSaveUnavailable):
            raise
        except Exception as exc:
            raise classify_research_design_revision_failure(exc) from exc

    try:
        stable_request_id = request_id or (
            f"agent-revision:r{expected_task_revision}:{instruction_digest}:{uuid4().hex}"
        )
        return service.revise(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            request_id=stable_request_id,
            expected_task_revision=expected_task_revision,
            provider=provider,
            user_input=raw_instruction,
            user_input_source_ref=stable_request_id,
        )
    except TaskRevisionConflictError:
        raise
    except (ResearchDesignRevisionUserError, ResearchDesignRevisionSaveUnavailable):
        raise
    except Exception as exc:
        raise classify_research_design_revision_failure(exc) from exc


__all__ = [
    "revise_research_design_with_agent",
]

"""Use production frozen input/runtime checks for a declared internal tool."""

from collections.abc import Callable, Mapping
from typing import Any

from app.execution.durable_llm_execution_policy import (
    normalize_resolved_durable_llm_execution_policy,
    resolve_durable_llm_execution_policy,
)
from app.execution.durable_skill_runtime import resolve_registered_durable_skill_runtime
from app.execution.research_input_contract_gate import (
    SkillInputRequest,
    validate_registered_skill_input_snapshot,
)
from app.execution.research_internal_skill_call import InternalSkillCallError
from app.execution.skill_contract_policy import validate_action_input
from app.modules.skill_binding_source import canonical_binding_handoff_evidence


def preflight_internal_skill_call(
    request: Mapping[str, Any],
    *,
    task: Any,
    snapshot: Mapping[str, Any],
    scope_check: Callable[[Mapping[str, Any], Any], None],
) -> Mapping[str, Any]:
    """No provider or tool I/O; scope_check belongs to the registered caller.

    It must constrain action inputs to the original confirmed inputs/targets.
    Frozen Binding grants identify tools, not arbitrary action destinations.
    """
    definition = request["snapshotDefinition"]
    runtime = resolve_registered_durable_skill_runtime(
        request["skillId"],
        definition_version=request["definitionVersion"],
        snapshot_definition=dict(definition),
        frozen_execution_policy_present=request.get("resolvedExecutionPolicy") is not None,
    )
    if runtime is None or runtime["runtimeKind"] not in {
        "deterministic",
        "external_adapter",
        "llm_reasoning",
    }:
        raise InternalSkillCallError("internal_skill_child_runtime_unavailable")
    bindings = request["selectedSkillBindings"]
    if len(bindings) != 1:
        raise InternalSkillCallError("internal_skill_child_binding_ambiguous")
    binding = bindings[0]
    decision = next(
        row
        for row in task.execution_decisions
        if row.execution_decision_id == request["executionDecisionId"]
    )
    parameters = validate_registered_skill_input_snapshot(
        snapshot=snapshot,
        request=SkillInputRequest(
            task_id=task.task_id,
            strategy=decision.strategy.value,
            skill_id=request["skillId"],
            definition_version=request["definitionVersion"],
        ),
        resolved_input=request["parameters"],
        skill_binding=binding,
    )
    if runtime["requiresExternalAction"]:
        validate_action_input(definition, parameters)
        scope_check({**request, "parameters": parameters}, task)
    if "resolvedExecutionPolicy" in request:
        policy = request["resolvedExecutionPolicy"]
        policy = (
            normalize_resolved_durable_llm_execution_policy(policy) if policy is not None else None
        )
    else:
        policy = resolve_durable_llm_execution_policy(definition)
    if runtime["runtimeKind"] == "llm_reasoning" and policy is None:
        raise InternalSkillCallError("internal_skill_child_execution_policy_missing")
    return {
        **runtime,
        "parameters": parameters,
        "resolvedExecutionPolicy": policy,
        "bindingInputEvidence": canonical_binding_handoff_evidence(binding),
    }

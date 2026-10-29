"""Skill-owned Agent feedback loop with a final formal deterministic closure.

The injected invoke function is the canonical internal tool command, never an
adapter or Provider bypass. Production registration/entry wiring is separate.
"""

import base64
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from adapter.correction_scope import (
    RepairCorrectionScopeError,
    install_verified_correction,
)
from adapter.input_materialization import (
    RepairInputFeedback,
    install_source_tool_evidence,
    materialize_source_tool_parameters,
)


class RepairImplementationError(RuntimeError):
    """Missing runtime/evidence is an implementation gap, not research exhaustion."""


class RepairPendingExecution(RuntimeError):
    """Existing Dispatch recovery must retain the parent while a child is active."""

    def __init__(self, observations):
        self.observations = deepcopy(observations)
        super().__init__("repair_child_execution_pending")


@dataclass(frozen=True)
class RepairTool:
    skill_id: str
    binding_ref: Mapping[str, Any]
    result_field: str | None = None
    input_schema: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RepairTools:
    reasoner: RepairTool
    readiness: RepairTool
    closure: RepairTool
    actions: tuple[RepairTool, ...]


def decoded_formal_tool_output(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if response.get("state") != "succeeded":
        return None
    if not response.get("capabilityCallId") or not response.get("resultId"):
        raise RepairImplementationError("repair_tool_canonical_evidence_missing")
    artifact = response.get("artifactPayload")
    if not isinstance(artifact, Mapping):
        raise RepairImplementationError("repair_tool_artifact_missing")
    try:
        blob = base64.b64decode(artifact["contentBase64"], validate=True)
        digest = artifact["presentation"]["contentSha256"]
        value = json.loads(blob)
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairImplementationError("repair_tool_artifact_invalid") from exc
    if digest != sha256(blob).hexdigest() or not isinstance(value, Mapping):
        raise RepairImplementationError("repair_tool_artifact_hash_or_shape_invalid")
    return value


def _observe(tool, parameters, invoke, observations):
    response = invoke(tool.binding_ref, deepcopy(dict(parameters)))
    if not isinstance(response, Mapping) or response.get("state") not in {
        "succeeded",
        "failed",
        "unknown",
        "running",
    }:
        raise RepairImplementationError("repair_tool_response_invalid")
    observation = {
        "skillId": tool.skill_id,
        "bindingRef": dict(tool.binding_ref),
        "parameters": deepcopy(dict(parameters)),
        "response": deepcopy(dict(response)),
    }
    observations.append(observation)
    return response, decoded_formal_tool_output(response)


def _observe_action(tool, proposal, context, invoke, observations):
    try:
        parameters = materialize_source_tool_parameters(tool.skill_id, proposal, context)
    except RepairInputFeedback as exc:
        observations.append(
            {
                "skillId": tool.skill_id,
                "bindingRef": dict(tool.binding_ref),
                "parameters": {},
                "response": {
                    "state": "failed",
                    "reasonCode": str(exc),
                    "submissionOccurred": False,
                },
            }
        )
        return observations[-1]["response"], None
    return _observe(tool, parameters, invoke, observations)


def _install_action_output(tool, output, context, original_inputs, original_source, observations):
    if tool.skill_id == "execution_plan_fact_assembly" and output.get("state") == "succeeded":
        try:
            install_verified_correction(
                original_inputs,
                output,
                context,
                original_source=original_source,
            )
        except RepairCorrectionScopeError as exc:
            # The assembler's canonical result remains succeeded. Scope
            # feedback means it cannot authorize a changed action destination.
            observations[-1]["response"]["actionScopeFeedback"] = str(exc)
        return
    if install_source_tool_evidence(tool.skill_id, output, context):
        return
    if output.get("state") == "succeeded" and tool.result_field:
        context[tool.result_field] = deepcopy(dict(output))


def _tool_catalog(tools: RepairTools):
    rows = [tools.reasoner, tools.readiness, tools.closure, *tools.actions]
    identities = [(row.binding_ref["bindingId"], row.binding_ref["bindingVersion"]) for row in rows]
    if len(set(identities)) != len(identities):
        raise RepairImplementationError("repair_tool_binding_ambiguous")
    action_ids = [row.skill_id for row in tools.actions]
    if len(set(action_ids)) != len(action_ids):
        raise RepairImplementationError("repair_action_skill_ambiguous")
    return [
        dict(
            skill_id=row.skill_id,
            binding_ref=dict(row.binding_ref),
            input_schema=deepcopy(row.input_schema),
        )
        for row in tools.actions
    ]


def _readiness_parameters(context):
    fields = {
        "reproduction_experiment_design",
        "reproduction_baseline_and_criteria",
        "execution_plan_facts",
        "code_identity",
        "resource_identities",
        "environment_record",
    }
    return {key: deepcopy(value) for key, value in context.items() if key in fields}


def _closure_parameters(context):
    fields = {
        "reproduction_experiment_design",
        "execution_plan_facts",
        "execution_readiness_record",
        "code_identity",
        "resource_identities",
        "environment_record",
    }
    return {key: deepcopy(value) for key, value in context.items() if key in fields}


def _preparation_report(observations, reason):
    # This is failed preparation, never a successful frozen execution input.
    return {
        "schema_version": "openair-reproduction-input-preparation-report/v1",
        "state": "failed",
        "input_status": "not_ready",
        "reason": reason,
        "observations": observations,
        "execution_claim": "No training, evaluation or experiment is claimed by this preparation.",
    }


def _agent_feedback(observations):
    # Do not recursively embed earlier reasoning prompts in each new prompt.
    return [
        {"skill_id": row["skillId"], "response": deepcopy(row["response"])} for row in observations
    ]


def run_input_repair(
    original_inputs: Mapping[str, Any],
    *,
    tools: RepairTools,
    invoke: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    max_calls: int,
    source_context: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Return tool failures to Agent; only actual closure can supply frozen input.

    max_calls comes from the frozen capability declaration. Each invocation also
    passes canonical budget/fence checks. It is not a new retry policy. Two slots
    are kept for readiness and the deterministic closure, within that same grant.
    """
    if type(max_calls) is not int or max_calls < 3:
        raise RepairImplementationError("repair_frozen_call_limit_invalid")
    catalog = _tool_catalog(tools)
    context = deepcopy(dict(original_inputs))
    if source_context is not None:
        if set(source_context) != {"source_package", "reference_metrics", "research_goal"}:
            raise RepairImplementationError("repair_code_owned_source_context_invalid")
        context.update(deepcopy(dict(source_context)))
    original_source = deepcopy((source_context or original_inputs).get("source_package"))
    observations = []
    reason = "The original preparation is incomplete."
    while len(observations) + 3 <= max_calls:
        response, proposal = _observe(
            tools.reasoner,
            {
                "original_inputs": deepcopy(dict(original_inputs)),
                "current_inputs": context,
                "tool_catalog": catalog,
                "observations": _agent_feedback(observations),
            },
            invoke,
            observations,
        )
        if response["state"] == "running":
            raise RepairPendingExecution(observations)
        if response["state"] == "unknown":
            return _preparation_report(observations, "Reasoning result is unknown.")
        if proposal is None:
            # Known reasoning failure is feedback in the next admitted call.
            continue
        if proposal.get("action") == "finish":
            reason = str(proposal.get("reason") or reason)
            break
        tool = next(
            (row for row in tools.actions if row.skill_id == proposal.get("skill_id")), None
        )
        parameters = proposal.get("parameters")
        if tool is None or not isinstance(parameters, Mapping):
            raise RepairImplementationError("repair_reasoner_command_contract_invalid")
        # Preserve space for code-owned final verification. No action is silently
        # launched after its original declared allowance has been consumed.
        if len(observations) + 3 > max_calls:
            reason = "The frozen internal tool allowance is exhausted."
            break
        response, output = _observe_action(tool, parameters, context, invoke, observations)
        if response["state"] == "running":
            raise RepairPendingExecution(observations)
        if response["state"] == "unknown":
            return _preparation_report(
                observations, "An action needs reconciliation; it was not repeated."
            )
        if output is not None:
            # The model cannot submit replacement identity facts. Only the
            # registered tool's verified artifact becomes closure material.
            _install_action_output(
                tool,
                output,
                context,
                original_inputs,
                original_source,
                observations,
            )
    return _verify_preparation(context, tools, invoke, observations, max_calls, reason)


def _verify_preparation(context, tools, invoke, observations, max_calls, reason):
    if len(observations) + 2 > max_calls:
        return _preparation_report(observations, "The frozen internal tool allowance is exhausted.")
    response, readiness = _observe(
        tools.readiness, _readiness_parameters(context), invoke, observations
    )
    if response["state"] == "running":
        raise RepairPendingExecution(observations)
    if response["state"] == "unknown":
        return _preparation_report(observations, "Readiness evidence is unknown.")
    if readiness is not None:
        context["execution_readiness_record"] = deepcopy(dict(readiness))
    response, closure = _observe(tools.closure, _closure_parameters(context), invoke, observations)
    if response["state"] == "running":
        raise RepairPendingExecution(observations)
    if closure is not None and closure.get("state") == "succeeded":
        # Preserve the original closure schema exactly. Provenance stays in the
        # canonical child Results/CapabilityCalls, not new fabricated fields.
        return closure
    return _preparation_report(observations, reason)


def run_input_repair_controller(original_inputs, *, tools, invoke, max_calls, source_context=None):
    """Subprocess entry: preserve active child control instead of throwing it away."""
    from app.execution.research_internal_loop_pending import pending_controller_output

    try:
        return run_input_repair(
            original_inputs,
            tools=tools,
            invoke=invoke,
            max_calls=max_calls,
            source_context=source_context,
        )
    except RepairPendingExecution as pending:
        return pending_controller_output(pending.observations)

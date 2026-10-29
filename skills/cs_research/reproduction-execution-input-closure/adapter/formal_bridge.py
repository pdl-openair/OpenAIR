"""Skill-owned bridge to frozen tool grants and canonical internal execution."""

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.execution.research_input_contract_gate import InputContractGateFailure
from app.execution.research_internal_skill_call import InternalSkillCallError, _declaration, _target
from app.execution.research_internal_skill_call_budget import InternalCallBudgetError
from app.execution.research_internal_skill_call_executor import execute_internal_skill_command

from adapter.action_overlap import require_action_target_available
from adapter.correction_scope import (
    RepairCorrectionScopeError,
    install_verified_correction,
)
from adapter.input_materialization import (
    SOURCE_TOOL_FIELDS,
    RepairInputFeedback,
    install_source_tool_evidence,
    materialize_source_tool_parameters,
)
from adapter.input_repair import (
    RepairImplementationError,
    RepairTool,
    RepairTools,
    decoded_formal_tool_output,
)

_ACTION_FIELDS = {
    "repository_revision_acquisition": "code_identity",
    "resource_identity_acquisition": "resource_identities",
    "environment_setup": "environment_record",
}
_INTERNAL_ROLES = {
    "reproduction_execution_input_repair_reasoning": "reasoner",
    "execution_readiness_assessment": "readiness",
    "reproduction_execution_input_closure": "closure",
    "reproduction_execution_input_validation": "closure",
}


def frozen_repair_tools(snapshot, parent_definition, work):
    declaration = _declaration(parent_definition)
    roles = {}
    actions = []
    for reference in declaration["binding_refs"]:
        binding, definition = _target(snapshot, reference, declaration, work)
        skill_id = binding["skillId"]
        schema = (
            definition.get("sourceContract", {})
            .get("frozenSchemas", {})
            .get("input", {})
            .get("schema")
        )
        if not isinstance(schema, Mapping):
            raise RepairImplementationError("repair_frozen_tool_input_schema_missing")
        tool = RepairTool(
            skill_id,
            deepcopy(dict(reference)),
            _ACTION_FIELDS.get(skill_id),
            deepcopy(dict(schema)),
        )
        if skill_id in _INTERNAL_ROLES:
            role = _INTERNAL_ROLES[skill_id]
            if role in roles:
                raise RepairImplementationError("repair_frozen_role_ambiguous")
            roles[role] = tool
        elif skill_id in _ACTION_FIELDS:
            actions.append(tool)
        elif skill_id in SOURCE_TOOL_FIELDS:
            actions.append(tool)
        else:
            raise RepairImplementationError("repair_frozen_tool_not_supported")
    if set(roles) != {"reasoner", "readiness", "closure"} or not actions:
        raise RepairImplementationError("repair_frozen_tool_supply_incomplete")
    return RepairTools(**roles, actions=tuple(actions)), declaration["max_calls"]


def original_action_scope(request: Mapping[str, Any], original_inputs: Mapping[str, Any]):
    """Check an action against the plan with established correction scope.

    Initial inputs are the frozen original plan. Only an integrity-checked formal
    assembler result with original target/pin/program proof can replace its
    machine parameters in the bridge. Pure reasoning never grants action scope.
    """
    plan = original_inputs.get("execution_plan_facts")
    if not isinstance(plan, Mapping):
        raise InternalSkillCallError("repair_original_plan_missing")
    skill_id = request["skillId"]
    parameters = request["parameters"]
    if skill_id == "repository_revision_acquisition":
        source = plan.get("source_identity")
        if not isinstance(source, Mapping):
            raise InternalSkillCallError("repair_original_code_target_missing")
        expected = {
            "repository": source.get("repository"),
            "revision": source.get("resolved_commit_sha"),
        }
    elif skill_id == "resource_identity_acquisition":
        requests = plan.get("resource_acquisition_requests")
        if not isinstance(requests, list) or not requests:
            raise InternalSkillCallError("repair_original_resource_targets_missing")
        selected = parameters.get("resources")
        if (
            set(parameters) != {"resources"}
            or not isinstance(selected, list)
            or not selected
            or any(row not in requests for row in selected)
            or len({row["usage"] for row in selected}) != len(selected)
        ):
            raise InternalSkillCallError("repair_action_outside_original_proven_scope")
        # Known failed resources may be corrected/acquired separately; successful
        # resources remain evidence. This never grants a new target or pin.
        return
    elif skill_id == "environment_setup":
        fields = {
            "python_version",
            "requirements",
            "verify_imports",
            "find_links",
            "dependency_install_timeout_seconds",
            "runtime_readiness_probe",
        }
        expected = {key: value for key, value in plan.items() if key in fields}
    else:
        raise InternalSkillCallError("repair_external_action_not_declared")
    if parameters != expected:
        raise InternalSkillCallError("repair_action_outside_original_proven_scope")


def _command_error_feedback(repository, owner, binding_ref, parameters, error):
    task = repository.load_read_only(
        task_id=owner.task_id,
        owner_id=owner.owner_id,
        workspace_id=owner.workspace_id,
    )
    wire = json.dumps(parameters, sort_keys=True, ensure_ascii=False, allow_nan=False)
    children = []
    for call in task.capability_calls:
        frozen = call.internal_call_input_snapshot
        if (
            frozen is not None
            and frozen.parent_capability_call_id == owner.parent_call_id
            and frozen.source_binding_ref == dict(binding_ref)
            and json.dumps(frozen.parameters, sort_keys=True, ensure_ascii=False, allow_nan=False)
            == wire
        ):
            children.append(call)
    if len(children) > 1:
        raise RepairImplementationError("repair_error_command_identity_ambiguous") from error
    reason = getattr(error, "code", str(error))
    if not children:
        return {
            "state": "failed",
            "failureClassification": "internal_tool_admission_rejected",
            "reasonCode": reason,
            "submissionOccurred": False,
        }
    child = children[0]
    state = {
        "queued": "running",
        "running": "running",
        "external_result_unknown": "unknown",
        "failed": "failed",
    }.get(child.status.value)
    if state is None:
        raise RepairImplementationError("repair_saved_tool_receipt_unreadable") from error
    return {
        "state": state,
        "capabilityCallId": child.capability_call_id,
        "failureClassification": "internal_tool_local_processing_error",
        "reasonCode": reason,
    }


def formal_repair_invoker(
    repository: Any,
    *,
    owner: Any,
    original_inputs: Mapping[str, Any],
    source_context=None,
):
    frozen_inputs = deepcopy(dict(original_inputs))
    effective_inputs = deepcopy(frozen_inputs)
    if source_context is not None:
        if set(source_context) != {"source_package", "reference_metrics", "research_goal"}:
            raise RepairImplementationError("repair_code_owned_source_context_invalid")
        effective_inputs.update(deepcopy(dict(source_context)))
    original_source = deepcopy((source_context or frozen_inputs).get("source_package"))

    def scope_check(request, _task):
        original_action_scope(request, effective_inputs)
        require_action_target_available(request, _task)

    def invoke(binding_ref, parameters):
        declaration_task = repository.load_read_only(
            task_id=owner.task_id,
            owner_id=owner.owner_id,
            workspace_id=owner.workspace_id,
        )
        parent = next(
            row
            for row in declaration_task.capability_calls
            if row.capability_call_id == owner.parent_call_id
        )
        work = next(
            row for row in declaration_task.work_units if row.work_unit_id == parent.work_unit_id
        )
        decision = next(
            row
            for row in declaration_task.execution_decisions
            if row.execution_decision_id == work.execution_decision_id
        )
        snapshot = repository.load_registry_snapshot(decision.registry_snapshot_ref.content_hash)
        definitions = snapshot["definitions"]["skills"]
        parent_definition = next(
            row
            for row in definitions
            if row["skillId"] == parent.capability_id
            and row["definitionVersion"] == work.executor_ref.definition_version
        )
        binding, _ = _target(snapshot, binding_ref, _declaration(parent_definition), work)
        skill_id = binding["skillId"]
        try:
            parameters = materialize_source_tool_parameters(skill_id, parameters, effective_inputs)
        except RepairInputFeedback as exc:
            return {"state": "failed", "reasonCode": str(exc), "submissionOccurred": False}
        try:
            response = execute_internal_skill_command(
                repository,
                owner=owner,
                binding_ref=binding_ref,
                parameters=parameters,
                scope_check=scope_check,
            )
        except (InternalSkillCallError, InternalCallBudgetError, InputContractGateFailure) as exc:
            # A local processing error may occur after real I/O. Read canonical
            # child state before claiming no submission or terminal failure.
            return _command_error_feedback(repository, owner, binding_ref, parameters, exc)
        output = decoded_formal_tool_output(response)
        if output is not None:
            _install_bridge_output(
                skill_id, output, response, frozen_inputs, effective_inputs, original_source
            )
        return response

    return invoke


def _install_bridge_output(skill_id, output, response, original, effective, source):
    if skill_id == "execution_plan_fact_assembly" and output.get("state") == "succeeded":
        try:
            install_verified_correction(original, output, effective, original_source=source)
        except RepairCorrectionScopeError as exc:
            response["actionScopeFeedback"] = str(exc)
        return
    install_source_tool_evidence(skill_id, output, effective)

"""Private dispatch context and unchanged direct-Skill request projection."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DispatchSubmissionRequest:
    dispatch_id: str
    task_id: str
    execution_decision_id: str
    work_unit_id: str
    idempotency_key: str
    owner_id: str
    workspace_id: str
    payload: Mapping[str, Any] = field(repr=False)
    capability_call_id: str = ""
    attempt_number: int = 0
    lease_owner: str = ""


def submission_request_from_authorized(
    fence: Any, *, owner_id: str, workspace_id: str
) -> DispatchSubmissionRequest:
    return DispatchSubmissionRequest(
        dispatch_id=fence.dispatch_id,
        task_id=fence.task_id,
        execution_decision_id=fence.execution_decision_id,
        work_unit_id=fence.work_unit_id,
        idempotency_key=fence.idempotency_key,
        owner_id=owner_id,
        workspace_id=workspace_id,
        payload=fence.work_unit_payload,
        capability_call_id=fence.external_attempt.capability_call_id,
        attempt_number=fence.external_attempt.attempt_number,
        lease_owner=str(getattr(fence, "lease_owner", "") or ""),
    )


def build_direct_skill_execution_request(
    request: DispatchSubmissionRequest,
    frozen: Any,
    parameters: Mapping[str, Any],
    binding_input_evidence: Any,
    snapshot_definition: Mapping[str, Any],
) -> dict[str, Any]:
    execution_request = {
        "skillId": frozen.skill_id,
        "taskId": request.task_id,
        "ownerId": request.owner_id,
        "workspaceId": request.workspace_id,
        "workUnitId": request.work_unit_id,
        "callId": request.idempotency_key,
        "capabilityCallId": request.capability_call_id,
        "executionDecisionId": request.execution_decision_id,
        "attemptNumber": request.attempt_number,
        "requestingActorType": "orchestrator",
        "requestingActorId": "openair_orchestrator",
        "frozenGoal": frozen.orchestrationPlan["instruction"],
        "frozenConstraints": frozen.orchestrationPlan["stopConditions"],
        "controllerInputContext": frozen.orchestrationPlan["inputRefs"],
        "parameters": dict(parameters),
        "logicalProviderId": frozen.logicalProviderId,
        "modelProfile": frozen.modelProfile,
        **(
            {"resolvedExecutionPolicy": dict(frozen.resolvedExecutionPolicy)}
            if isinstance(frozen.resolvedExecutionPolicy, Mapping)
            else {}
        ),
        "providerEvidence": {
            "capabilityCallId": request.capability_call_id,
            "attemptNumber": request.attempt_number,
        },
        **(
            {"executionCheckpointId": request.capability_call_id}
            if request.capability_call_id
            else {}
        ),
        **(
            {"bindingInputEvidence": dict(binding_input_evidence)}
            if isinstance(binding_input_evidence, Mapping)
            else {}
        ),
    }
    source = snapshot_definition.get("sourceContract") or {}
    runtime = source.get("runtime_contract") or {}
    if runtime.get("internal_skill_calls") is not None:
        if not request.lease_owner or not request.capability_call_id or request.attempt_number < 1:
            raise ValueError("dispatch_internal_owner_context_missing")
        execution_request["_internalSkillExecutionOwner"] = {
            "task_id": request.task_id,
            "owner_id": request.owner_id,
            "workspace_id": request.workspace_id,
            "dispatch_id": request.dispatch_id,
            "parent_call_id": request.capability_call_id,
            "lease_owner": request.lease_owner,
            "attempt_number": request.attempt_number,
        }
    return execution_request

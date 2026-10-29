"""Carry an unfinished Skill controller through the existing running outcome.

This is private execution control, never a scientific result or a remote run ID.
The parent checkpoint remains prepared: recovery re-enters the logical loop and
canonical child commands recover their saved evidence without physical replay.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.execution.research_internal_skill_call import (
    InternalSkillCallError,
    prepare_internal_skill_call,
)

_CONTROL = "openair_internal_execution_pending_v1"


class DispatchProviderInternalExecutionPending(RuntimeError):
    """A formally persisted child requires continuation of the original owner."""

    def __init__(self, child_call_id: str):
        self.child_call_id = child_call_id
        super().__init__("internal_skill_execution_pending")


def pending_controller_output(observations):
    if not observations:
        raise InternalSkillCallError("internal_loop_pending_evidence_missing")
    response = observations[-1].get("response", {})
    child_id = response.get("capabilityCallId")
    if response.get("state") != "running" or not isinstance(child_id, str) or not child_id.strip():
        raise InternalSkillCallError("internal_loop_pending_evidence_invalid")
    return {"executionControl": _CONTROL, "pendingCapabilityCallId": child_id}


def pending_runtime_output(request, runtime, output):
    """Recognize only the declared controller's private machine envelope."""
    if not isinstance(output, Mapping) or output.get("executionControl") != _CONTROL:
        return None
    if (
        set(output) != {"executionControl", "pendingCapabilityCallId"}
        or runtime.get("runtimeKind") != "reasoning_adapter"
        or not isinstance(request.get("_internalSkillExecutionOwner"), Mapping)
        or not isinstance(output["pendingCapabilityCallId"], str)
        or not output["pendingCapabilityCallId"].strip()
    ):
        raise InternalSkillCallError("internal_loop_pending_context_invalid")
    # The provider must verify this child against canonical frozen evidence.
    return {"state": "running", **output}


def validate_pending_submission(result: Any, *, request: Any, repository: Any):
    if not isinstance(result, Mapping) or result.get("executionControl") != _CONTROL:
        return None
    if (
        set(result) != {"state", "executionControl", "pendingCapabilityCallId"}
        or result["state"] != "running"
    ):
        raise InternalSkillCallError("internal_loop_pending_result_invalid")
    task = repository.load_read_only(
        task_id=request.task_id, owner_id=request.owner_id, workspace_id=request.workspace_id
    )
    matches = [
        row
        for row in task.capability_calls
        if row.capability_call_id == result["pendingCapabilityCallId"]
    ]
    if len(matches) != 1:
        raise InternalSkillCallError("internal_loop_pending_child_missing")
    child = matches[0]
    frozen = child.internal_call_input_snapshot
    if (
        frozen is None
        or frozen.parent_capability_call_id != request.capability_call_id
        or child.work_unit_id != request.work_unit_id
        or not request.lease_owner
        or request.attempt_number < 1
    ):
        raise InternalSkillCallError("internal_loop_pending_lineage_invalid")
    prepared = prepare_internal_skill_call(
        task=task,
        owner_id=request.owner_id,
        workspace_id=request.workspace_id,
        parent_call_id=request.capability_call_id,
        snapshot=repository.load_registry_snapshot(frozen.registry_snapshot_hash),
        snapshot_hash=frozen.registry_snapshot_hash,
        binding_ref=frozen.source_binding_ref,
        parameters=frozen.parameters,
        occurred_at=datetime.now(UTC),
        execution_stop_active=False,
    )
    if not prepared.replay or prepared.call.capability_call_id != child.capability_call_id:
        raise InternalSkillCallError("internal_loop_pending_command_invalid")
    # A child may have completed after the subprocess produced the envelope.
    # Preserve the unfinished parent; its next logical entry consumes that result.
    return DispatchProviderInternalExecutionPending(child.capability_call_id)

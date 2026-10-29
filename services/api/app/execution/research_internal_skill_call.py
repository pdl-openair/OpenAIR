"""Prepare a canonical child call from a frozen capability tool declaration.

Preparation has no persistence or execution authority. The transaction caller
must recheck the parent, reserve the confirmed project budget and persist the
call before giving this request to the production runtime.
"""

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from app.models.research_orchestration import (
    CapabilityCallStatus,
    CapabilityCallV12,
    ExecutionDecisionStatus,
    ResearchTaskV12,
    TaskStatus,
    WorkUnitStatus,
)


class InternalSkillCallError(ValueError):
    """A child request has no valid frozen authority; no action was attempted."""


@dataclass(frozen=True)
class PreparedInternalSkillCall:
    call: CapabilityCallV12
    request: Mapping[str, Any]
    request_hash: str
    parent_call_id: str
    replay: bool


def _one(rows: Any, **identity: Any) -> Mapping[str, Any]:
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and all(row.get(key) == value for key, value in identity.items())
    ]
    if len(matches) != 1 or matches[0].get("enabled") is not True:
        raise InternalSkillCallError("internal_skill_frozen_identity_invalid")
    return matches[0]


def _declaration(definition: Mapping[str, Any]) -> Mapping[str, Any]:
    source = definition.get("sourceContract") or {}
    if not isinstance(source, Mapping):
        raise InternalSkillCallError("internal_skill_frozen_grant_invalid")
    runtime = source.get("runtime_contract") or {}
    if not isinstance(runtime, Mapping):
        raise InternalSkillCallError("internal_skill_frozen_grant_invalid")
    declaration = runtime.get("internal_skill_calls")
    keys = {"contract_version", "binding_refs", "max_calls"}
    if not isinstance(declaration, Mapping) or set(declaration) != keys:
        raise InternalSkillCallError("internal_skill_frozen_grant_missing")
    refs, limit = declaration["binding_refs"], declaration["max_calls"]
    if (
        declaration["contract_version"] != 1
        or isinstance(declaration["contract_version"], bool)
        or not isinstance(refs, list)
        or not refs
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
    ):
        raise InternalSkillCallError("internal_skill_frozen_grant_invalid")
    identities = []
    for ref in refs:
        if (
            not isinstance(ref, Mapping)
            or set(ref) != {"bindingId", "bindingVersion"}
            or not isinstance(ref["bindingId"], str)
            or not ref["bindingId"].strip()
            or isinstance(ref["bindingVersion"], bool)
            or not isinstance(ref["bindingVersion"], int)
            or ref["bindingVersion"] < 1
        ):
            raise InternalSkillCallError("internal_skill_frozen_grant_invalid")
        identities.append((ref["bindingId"], ref["bindingVersion"]))
    if len(identities) != len(set(identities)):
        raise InternalSkillCallError("internal_skill_frozen_grant_ambiguous")
    return declaration


def _active_parent(task: ResearchTaskV12, parent_call_id: str) -> tuple[Any, Any, Any]:
    if task.status is not TaskStatus.RUNNING:
        raise InternalSkillCallError("internal_skill_task_not_active")
    parents = [row for row in task.capability_calls if row.capability_call_id == parent_call_id]
    if len(parents) != 1 or parents[0].status is not CapabilityCallStatus.RUNNING:
        raise InternalSkillCallError("internal_skill_parent_not_running")
    parent = parents[0]
    work = next((row for row in task.work_units if row.work_unit_id == parent.work_unit_id), None)
    if (
        work is None
        or work.status is not WorkUnitStatus.RUNNING
        or work.substage_instance_id != parent.substage_instance_id
    ):
        raise InternalSkillCallError("internal_skill_parent_work_unit_not_running")
    if (
        parent.capability_type.value != "skill"
        or parent.capability_id != work.executor_ref.executor_id
    ):
        raise InternalSkillCallError("internal_skill_parent_executor_mismatch")
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == work.execution_decision_id
        ),
        None,
    )
    if (
        decision is None
        or decision.status is not ExecutionDecisionStatus.DISPATCHED
        or decision.substage_instance_id != parent.substage_instance_id
    ):
        raise InternalSkillCallError("internal_skill_parent_decision_not_dispatched")
    return parent, work, decision


def _target(
    snapshot: Mapping[str, Any],
    binding_ref: Mapping[str, Any],
    declaration: Mapping[str, Any],
    work: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if dict(binding_ref) not in declaration["binding_refs"]:
        raise InternalSkillCallError("internal_skill_not_granted")
    binding = _one(snapshot.get("bindings", {}).get("skills", []), **dict(binding_ref))
    if (
        binding.get("definitionId") != work.substage_definition_id
        or binding.get("definitionVersion") != work.substage_definition_version
    ):
        raise InternalSkillCallError("internal_skill_binding_outside_responsibility")
    definition = _one(
        snapshot.get("definitions", {}).get("skills", []),
        skillId=binding["skillId"],
        definitionVersion=binding["skillDefinitionVersion"],
    )
    return binding, definition


def prepare_internal_skill_call(
    *,
    task: ResearchTaskV12,
    owner_id: str,
    workspace_id: str,
    parent_call_id: str,
    snapshot: Mapping[str, Any],
    snapshot_hash: str,
    binding_ref: Mapping[str, Any],
    parameters: Mapping[str, Any],
    occurred_at: datetime,
    execution_stop_active: bool,
    action_reconciliation_check: Callable | None = None,
) -> PreparedInternalSkillCall:
    """Prepare an immutable request; reuse the same identity for the same command."""
    if not owner_id.strip() or not workspace_id.strip():
        raise InternalSkillCallError("internal_skill_scope_missing")
    if execution_stop_active:
        raise InternalSkillCallError("internal_skill_execution_stopped")
    parent, work, decision = _active_parent(task, parent_call_id)
    if decision.registry_snapshot_ref.content_hash != snapshot_hash:
        raise InternalSkillCallError("internal_skill_snapshot_mismatch")
    parent_definition = _one(
        snapshot.get("definitions", {}).get("skills", []),
        skillId=parent.capability_id,
        definitionVersion=work.executor_ref.definition_version,
    )
    declaration = _declaration(parent_definition)
    binding, definition = _target(snapshot, binding_ref, declaration, work)
    command = {
        "taskId": task.task_id,
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "parentCallId": parent_call_id,
        "workUnitId": work.work_unit_id,
        "registrySnapshotHash": snapshot_hash,
        "bindingRef": dict(binding_ref),
        "parameters": deepcopy(dict(parameters)),
    }
    wire = json.dumps(
        command, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    digest = sha256(wire).hexdigest()
    key = f"internal-skill:{parent_call_id}:{digest}"
    existing = [row for row in task.capability_calls if row.idempotency_key == key]
    call_id = f"capcall:{digest}"
    runtime_id = f"registry:{binding['bindingId']}:{binding['bindingVersion']}"
    if len(existing) > 1:
        raise InternalSkillCallError("internal_skill_replay_ambiguous")
    if existing:
        call = existing[0]
        if (
            call.capability_call_id != call_id
            or call.work_unit_id != work.work_unit_id
            or call.capability_id != binding["skillId"]
            or call.runtime_binding_id != runtime_id
            or parent_call_id not in call.input_refs
        ):
            raise InternalSkillCallError("internal_skill_replay_conflict")
    else:
        children = [
            row
            for row in task.capability_calls
            if row.idempotency_key.startswith(f"internal-skill:{parent_call_id}:")
        ]
        action_runtime = definition.get("sourceContract", {}).get("runtime_contract", {})
        if (
            action_reconciliation_check is None
            and action_runtime.get("requires_external_action") is True
            and any(
                row.status is CapabilityCallStatus.EXTERNAL_RESULT_UNKNOWN
                and row.runtime_binding_id == runtime_id
                for row in children
            )
        ):
            raise InternalSkillCallError("internal_skill_unknown_action_requires_reconciliation")
        if len(children) >= declaration["max_calls"]:
            raise InternalSkillCallError("internal_skill_declared_calls_exhausted")
        call = CapabilityCallV12(
            capabilityCallId=call_id,
            idempotencyKey=key,
            attemptNumber=1,
            workUnitId=work.work_unit_id,
            substageInstanceId=work.substage_instance_id,
            requestingActorRef=parent.requesting_actor_ref,
            capabilityType="skill",
            capabilityId=binding["skillId"],
            runtimeBindingId=runtime_id,
            status="queued",
            inputRefs=[parent_call_id],
            outputArtifactRefs=[],
            startedAt=occurred_at,
            finishedAt=None,
        )
    request = {
        "skillId": binding["skillId"],
        "taskId": task.task_id,
        "ownerId": owner_id,
        "workspaceId": workspace_id,
        "workUnitId": work.work_unit_id,
        "executionDecisionId": decision.execution_decision_id,
        "capabilityCallId": call_id,
        "parameters": deepcopy(dict(parameters)),
        "definitionVersion": binding["skillDefinitionVersion"],
        "snapshotDefinition": deepcopy(dict(definition)),
        "sourceBindingRefs": [dict(binding_ref)],
        "selectedSkillBindings": [deepcopy(dict(binding))],
    }
    if not existing and action_reconciliation_check is not None:
        action_runtime = definition.get("sourceContract", {}).get("runtime_contract", {})
        if action_runtime.get("requires_external_action") is True:
            action_reconciliation_check(request, task)
    return PreparedInternalSkillCall(call, request, digest, parent_call_id, bool(existing))

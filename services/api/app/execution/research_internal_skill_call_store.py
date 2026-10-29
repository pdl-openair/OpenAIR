"""Persist child reservations through the existing scoped Task transaction."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.execution.research_internal_skill_call import (
    InternalSkillCallError,
    PreparedInternalSkillCall,
    prepare_internal_skill_call,
)
from app.execution.research_internal_skill_call_budget import (
    InternalCallBudgetError,
    project_internal_call_budget,
    require_internal_call_budget,
)
from app.models.research_capability_call import (
    InternalCapabilityCallInput,
    frozen_input_content_hash,
)
from app.models.research_orchestration import ResearchTaskV12, ResultV1
from app.modules.external_execution_checkpoint import ExternalExecutionCheckpointRepository


@dataclass(frozen=True)
class InternalExecutionOwner:
    task_id: str
    owner_id: str
    workspace_id: str
    dispatch_id: str
    parent_call_id: str
    lease_owner: str
    attempt_number: int


@dataclass(frozen=True)
class BegunInternalSkillCall:
    prepared: PreparedInternalSkillCall
    execute: bool
    checkpoint_phase: str


def internal_call_checkpoint_body(prepared: PreparedInternalSkillCall) -> bytes:
    frozen = prepared.call.internal_call_input_snapshot
    if frozen is None:
        raise InternalSkillCallError("internal_skill_persisted_input_missing")
    return frozen.model_dump_json(by_alias=True).encode("utf-8")


def internal_call_checkpoint_kind(prepared: PreparedInternalSkillCall) -> str:
    kind = prepared.request["snapshotDefinition"]["sourceContract"]["runtime_contract"]["kind"]
    if kind == "external_adapter":
        return "external_adapter"
    if kind == "llm_reasoning":
        return "provider_http"
    if kind == "deterministic":
        return "direct_skill_runtime"
    raise InternalSkillCallError("internal_skill_child_runtime_unsupported")


def _restore_internal_request(
    repository: Any, task: ResearchTaskV12, owner: InternalExecutionOwner, child: Any
) -> PreparedInternalSkillCall:
    frozen = child.internal_call_input_snapshot
    if frozen is None:
        raise InternalSkillCallError("internal_skill_persisted_input_missing")
    restored = prepare_internal_skill_call(
        task=task,
        owner_id=owner.owner_id,
        workspace_id=owner.workspace_id,
        parent_call_id=owner.parent_call_id,
        snapshot=repository.load_registry_snapshot(frozen.registry_snapshot_hash),
        snapshot_hash=frozen.registry_snapshot_hash,
        binding_ref=frozen.source_binding_ref,
        parameters=frozen.parameters,
        occurred_at=datetime.now(UTC),
        execution_stop_active=False,
    )
    if restored.call.capability_call_id != child.capability_call_id:
        raise InternalSkillCallError("internal_skill_saved_command_identity_mismatch")
    request = {
        **restored.request,
        "resolvedExecutionPolicy": frozen.resolved_execution_policy,
        "executionTimeoutSeconds": frozen.execution_timeout_seconds,
    }
    return PreparedInternalSkillCall(
        child, request, restored.request_hash, owner.parent_call_id, True
    )


def _require_parent_dispatch(cursor: Any, owner: InternalExecutionOwner) -> Mapping[str, Any]:
    cursor.execute(
        """SELECT d.*, d.lease_expires_at > clock_timestamp() AS lease_live,
                  x.capability_call_id AS current_call_id,
                  x.attempt_number AS current_attempt_number
             FROM openair_dispatch d
             JOIN openair_external_execution x ON x.dispatch_id=d.dispatch_id
            WHERE d.task_id=%s AND d.dispatch_id=%s
              AND x.owner_id=%s AND x.workspace_id=%s
            FOR UPDATE OF d, x""",
        (owner.task_id, owner.dispatch_id, owner.owner_id, owner.workspace_id),
    )
    row = cursor.fetchone()
    if (
        row is None
        or row["status"] != "submission_authorized"
        or row["lease_owner"] != owner.lease_owner
        or not row["lease_live"]
        or row["external_action_started_at"] is None
        or row["current_call_id"] != owner.parent_call_id
        or row["current_attempt_number"] != owner.attempt_number
    ):
        raise InternalSkillCallError("internal_skill_parent_dispatch_fence_lost")
    return row


def _budget_facts(
    repository: Any, cursor: Any, task: ResearchTaskV12, snapshots: dict[str, Mapping[str, Any]]
) -> tuple[dict[str, str], dict[str, int]]:
    kinds = {}
    work = {row.work_unit_id: row for row in task.work_units}
    decisions = {row.execution_decision_id: row for row in task.execution_decisions}
    for call in task.capability_calls:
        unit = work[call.work_unit_id]
        reference = decisions[unit.execution_decision_id].registry_snapshot_ref.content_hash
        if reference not in snapshots:
            snapshots[reference] = repository.load_registry_snapshot(reference)
        definitions = snapshots[reference].get("definitions", {}).get("skills", [])
        frozen = call.internal_call_input_snapshot
        if frozen is None:
            version = unit.executor_ref.definition_version
        else:
            bindings = snapshots[reference].get("bindings", {}).get("skills", [])
            exact = [
                row
                for row in bindings
                if all(row.get(key) == value for key, value in frozen.source_binding_ref.items())
                and row.get("skillId") == call.capability_id
            ]
            if len(exact) != 1:
                raise InternalSkillCallError("internal_skill_budget_binding_ambiguous")
            version = exact[0]["skillDefinitionVersion"]
        rows = [
            row
            for row in definitions
            if row.get("skillId") == call.capability_id and row.get("definitionVersion") == version
        ]
        if len(rows) != 1:
            raise InternalSkillCallError("internal_skill_budget_definition_ambiguous")
        kinds[call.capability_call_id] = (
            rows[0].get("sourceContract", {}).get("runtime_contract", {}).get("kind")
        )
    cursor.execute(
        """SELECT parent_id, count(*) AS count FROM openair_provider_http_attempts
            WHERE parent_kind='capability_call' AND parent_id=ANY(%s)
            GROUP BY parent_id""",
        ([row.capability_call_id for row in task.capability_calls],),
    )
    return kinds, {row["parent_id"]: row["count"] for row in cursor.fetchall()}


def reserve_internal_skill_call(
    repository: Any,
    *,
    owner: InternalExecutionOwner,
    binding_ref: Mapping[str, Any],
    parameters: Mapping[str, Any],
    preflight: Callable[..., Mapping[str, Any]],
) -> PreparedInternalSkillCall:
    """Lock Task then original Dispatch; no provider/tool I/O is allowed here.

    preflight must prepare the production runtime, final machine input and
    action-scope checks without I/O. Reservation does not authorize execution;
    begin must repeat the ownership checks and persist a per-call checkpoint.
    """
    repository._require_write_enabled()
    repository._require_postgres()
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            task, raw = repository._load_locked_task(
                cursor,
                task_id=owner.task_id,
                owner_id=owner.owner_id,
                workspace_id=owner.workspace_id,
            )
            dispatch = _require_parent_dispatch(cursor, owner)
            parent = next(
                (
                    row
                    for row in task.capability_calls
                    if row.capability_call_id == owner.parent_call_id
                ),
                None,
            )
            if parent is None:
                raise InternalSkillCallError("internal_skill_parent_missing")
            unit = next(row for row in task.work_units if row.work_unit_id == parent.work_unit_id)
            decision = next(
                row
                for row in task.execution_decisions
                if row.execution_decision_id == unit.execution_decision_id
            )
            if (
                dispatch["work_unit_id"] != unit.work_unit_id
                or dispatch["execution_decision_id"] != decision.execution_decision_id
            ):
                raise InternalSkillCallError("internal_skill_parent_dispatch_lineage_mismatch")
            reference = decision.registry_snapshot_ref.content_hash
            snapshot = repository.load_registry_snapshot(reference)
            prepared = prepare_internal_skill_call(
                task=task,
                owner_id=owner.owner_id,
                workspace_id=owner.workspace_id,
                parent_call_id=owner.parent_call_id,
                snapshot=snapshot,
                snapshot_hash=reference,
                binding_ref=binding_ref,
                parameters=parameters,
                occurred_at=datetime.now(UTC),
                execution_stop_active=repository._execution_stop_active(raw),
                action_reconciliation_check=lambda request, task: preflight(
                    request, task=task, snapshot=snapshot
                ),
            )
            if prepared.replay:
                frozen = prepared.call.internal_call_input_snapshot
                if frozen is None:
                    raise InternalSkillCallError("internal_skill_persisted_input_missing")
                request = {
                    **prepared.request,
                    "parameters": dict(frozen.parameters),
                    "resolvedExecutionPolicy": frozen.resolved_execution_policy,
                    "executionTimeoutSeconds": frozen.execution_timeout_seconds,
                }
                return PreparedInternalSkillCall(
                    prepared.call, request, prepared.request_hash, prepared.parent_call_id, True
                )
            runtime = preflight(prepared.request, task=task, snapshot=snapshot)
            kinds, attempts = _budget_facts(repository, cursor, task, {reference: snapshot})
            consumed = project_internal_call_budget(
                task,
                runtime_kinds=kinds,
                provider_attempt_counts=attempts,
                observed_at=datetime.now(UTC),
                cost_amount=None,
            )
            remaining = require_internal_call_budget(
                boundary=decision.budget_snapshot.boundary,
                consumed=consumed,
                runtime_kind=str(runtime["runtimeKind"]),
            )
            policy = runtime.get("resolvedExecutionPolicy")
            if runtime["runtimeKind"] == "llm_reasoning" and not isinstance(policy, Mapping):
                raise InternalSkillCallError("internal_skill_child_execution_policy_missing")
            frozen_input = {
                "contractVersion": 1,
                "parentCapabilityCallId": owner.parent_call_id,
                "registrySnapshotHash": reference,
                "sourceBindingRef": dict(binding_ref),
                "parameters": dict(parameters),
                "resolvedExecutionPolicy": policy,
                "executionTimeoutSeconds": remaining,
            }
            frozen_input["contentHash"] = frozen_input_content_hash(frozen_input)
            frozen = InternalCapabilityCallInput.model_validate(frozen_input)
            call = prepared.call.model_copy(update={"internal_call_input_snapshot": frozen})
            payload = task.model_dump(mode="json", by_alias=True)
            payload["capabilityCalls"].append(call.model_dump(mode="json", by_alias=True))
            candidate = ResearchTaskV12.model_validate(payload)
            repository._write_locked_task(cursor, raw, candidate)
            # This is preparation metadata, never a physical execution receipt.
            request = {
                **prepared.request,
                "resolvedExecutionPolicy": policy,
                "executionTimeoutSeconds": remaining,
            }
            return PreparedInternalSkillCall(
                call, request, prepared.request_hash, prepared.parent_call_id, False
            )


def begin_internal_skill_call(
    repository: Any,
    *,
    owner: InternalExecutionOwner,
    prepared: PreparedInternalSkillCall,
) -> BegunInternalSkillCall:
    """Atomically claim an already prepared checkpoint and canonical child.

    This function performs no I/O outside the database. A submitted checkpoint
    never grants a second execution, including when the caller lost its receipt.
    Production input preflight must precede this call using the persisted input.
    """
    repository._require_write_enabled()
    repository._require_postgres()
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            task, raw = repository._load_locked_task(
                cursor,
                task_id=owner.task_id,
                owner_id=owner.owner_id,
                workspace_id=owner.workspace_id,
            )
            dispatch = _require_parent_dispatch(cursor, owner)
            if task.status.value != "running" or repository._execution_stop_active(raw):
                raise InternalSkillCallError("internal_skill_execution_stopped")
            # Revalidate canonical responsibility and compare with the saved input.
            parent = next(
                (
                    row
                    for row in task.capability_calls
                    if row.capability_call_id == owner.parent_call_id
                ),
                None,
            )
            child = next(
                (
                    row
                    for row in task.capability_calls
                    if row.capability_call_id == prepared.call.capability_call_id
                ),
                None,
            )
            if (
                parent is None
                or child is None
                or parent.status.value != "running"
                or child.work_unit_id != parent.work_unit_id
                or prepared.parent_call_id != owner.parent_call_id
                or child.internal_call_input_snapshot != prepared.call.internal_call_input_snapshot
            ):
                raise InternalSkillCallError("internal_skill_begin_authority_mismatch")
            unit = next(row for row in task.work_units if row.work_unit_id == parent.work_unit_id)
            decision = next(
                row
                for row in task.execution_decisions
                if row.execution_decision_id == unit.execution_decision_id
            )
            if (
                unit.status.value != "running"
                or decision.status.value != "dispatched"
                or dispatch["work_unit_id"] != unit.work_unit_id
                or dispatch["execution_decision_id"] != decision.execution_decision_id
            ):
                raise InternalSkillCallError("internal_skill_begin_responsibility_closed")
            prepared = _restore_internal_request(repository, task, owner, child)
            cursor.execute(
                """SELECT dispatch_id, request_hash, execution_kind, phase
                     FROM openair_external_execution_checkpoints
                    WHERE capability_call_id=%s FOR UPDATE""",
                (child.capability_call_id,),
            )
            checkpoint = cursor.fetchone()
            if (
                checkpoint is None
                or checkpoint["dispatch_id"] != owner.dispatch_id
                or checkpoint["execution_kind"] != internal_call_checkpoint_kind(prepared)
                or checkpoint["request_hash"]
                != sha256(internal_call_checkpoint_body(prepared)).hexdigest()
            ):
                raise InternalSkillCallError("internal_skill_begin_checkpoint_mismatch")
            phase = checkpoint["phase"]
            if phase != "prepared":
                return BegunInternalSkillCall(prepared, False, phase)
            if child.status.value != "queued":
                raise InternalSkillCallError("internal_skill_begin_status_conflict")
            if not ExternalExecutionCheckpointRepository._mark_submitted(
                cursor,
                capability_call_id=child.capability_call_id,
            ):
                raise InternalSkillCallError("internal_skill_begin_checkpoint_claim_lost")
            payload = task.model_dump(mode="json", by_alias=True)
            target = next(
                row
                for row in payload["capabilityCalls"]
                if row["capabilityCallId"] == child.capability_call_id
            )
            target.update(status="running", startedAt=datetime.now(UTC).isoformat())
            candidate = ResearchTaskV12.model_validate(payload)
            repository._write_locked_task(cursor, raw, candidate)
            return BegunInternalSkillCall(prepared, True, "submitted")


def _require_provider_io_admission(
    repository: Any,
    cursor: Any,
    task: ResearchTaskV12,
    raw: Any,
    owner: InternalExecutionOwner,
    child_call_id: str,
) -> float | None:
    _require_parent_dispatch(cursor, owner)
    if task.status.value != "running" or repository._execution_stop_active(raw):
        raise InternalSkillCallError("internal_skill_execution_stopped")
    child = next(
        (row for row in task.capability_calls if row.capability_call_id == child_call_id), None
    )
    if child is None or child.status.value != "running":
        raise InternalSkillCallError("internal_skill_provider_child_not_running")
    restored = _restore_internal_request(repository, task, owner, child)
    if internal_call_checkpoint_kind(restored) != "provider_http":
        raise InternalSkillCallError("internal_skill_provider_runtime_mismatch")
    unit = next(row for row in task.work_units if row.work_unit_id == child.work_unit_id)
    decision = next(
        row
        for row in task.execution_decisions
        if row.execution_decision_id == unit.execution_decision_id
    )
    kinds, attempts = _budget_facts(repository, cursor, task, {})
    consumed = project_internal_call_budget(
        task,
        runtime_kinds=kinds,
        provider_attempt_counts=attempts,
        observed_at=datetime.now(UTC),
        cost_amount=None,
    )
    return require_internal_call_budget(
        boundary=decision.budget_snapshot.boundary,
        consumed=consumed,
        runtime_kind="llm_reasoning",
        reserve_new_call=False,
    )


def authorize_internal_provider_io(
    repository: Any, *, owner: InternalExecutionOwner, child_call_id: str, attempt: Any
) -> float | None:
    """Recheck budget and original owner immediately before each physical POST.

    Provider begin has already committed the attempt. Counting all committed
    attempts under the Task lock prevents correction/batch attempts from hiding
    behind a single canonical call. Mark I/O in this same transaction.
    """
    repository._require_write_enabled()
    repository._require_postgres()
    denial = None
    remaining = None
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            task, raw = repository._load_locked_task(
                cursor,
                task_id=owner.task_id,
                owner_id=owner.owner_id,
                workspace_id=owner.workspace_id,
            )
            try:
                remaining = _require_provider_io_admission(
                    repository,
                    cursor,
                    task,
                    raw,
                    owner,
                    child_call_id,
                )
            except (InternalSkillCallError, InternalCallBudgetError) as exc:
                denial = exc
                # Record known pre-I/O denial only while no physical submission
                # has started. An existing running POST must never be rewritten.
                cursor.execute(
                    """UPDATE openair_provider_http_attempts
                          SET execution_state='failed', execution_reason='internal_call_io_denied',
                              execution_stage='authorization', finished_at=clock_timestamp(),
                              transport_outcome='transport_not_submitted', submission_proven=FALSE
                        WHERE attempt_id=%s AND parent_kind='capability_call' AND parent_id=%s
                          AND execution_state='running' AND io_started=FALSE""",
                    (attempt.attempt_id, child_call_id),
                )
            if denial is None:
                cursor.execute(
                    """UPDATE openair_provider_http_attempts
                      SET io_started=TRUE, execution_stage='transport'
                    WHERE attempt_id=%s AND parent_kind='capability_call'
                      AND parent_id=%s AND execution_state='running' AND io_started=FALSE
                   RETURNING attempt_id""",
                    (attempt.attempt_id, child_call_id),
                )
                if cursor.fetchone() is None:
                    raise InternalSkillCallError("internal_skill_provider_io_claim_lost")
    if denial is not None:
        raise denial
    return remaining


def finish_internal_skill_call(
    repository: Any,
    *,
    owner: InternalExecutionOwner,
    result: ResultV1,
    response_hash: str,
) -> ResultV1:
    """Preserve a verified response without advancing the parent responsibility.

    A stopped/lost parent may still receive evidence; this transaction grants
    no new action and never updates its Decision, WorkUnit or stop state.
    """
    repository._require_write_enabled()
    repository._require_postgres()
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            task, raw = repository._load_locked_task(
                cursor,
                task_id=owner.task_id,
                owner_id=owner.owner_id,
                workspace_id=owner.workspace_id,
            )
            child = next(
                (
                    row
                    for row in task.capability_calls
                    if row.capability_call_id == result.capability_call_id
                ),
                None,
            )
            frozen = child.internal_call_input_snapshot if child is not None else None
            if (
                child is None
                or frozen is None
                or frozen.parent_capability_call_id != owner.parent_call_id
                or child.work_unit_id != result.work_unit_id
            ):
                raise InternalSkillCallError("internal_skill_finish_responsibility_mismatch")
            cursor.execute(
                """SELECT c.*, d.task_id AS dispatch_task_id,
                          d.work_unit_id AS dispatch_work_unit_id
                     FROM openair_external_execution_checkpoints c
                     JOIN openair_dispatch d ON d.dispatch_id=c.dispatch_id
                    WHERE c.capability_call_id=%s AND c.dispatch_id=%s
                    FOR UPDATE OF c, d""",
                (child.capability_call_id, owner.dispatch_id),
            )
            checkpoint = cursor.fetchone()
            if (
                checkpoint is None
                or checkpoint["dispatch_task_id"] != owner.task_id
                or checkpoint["dispatch_work_unit_id"] != child.work_unit_id
                or checkpoint["phase"] not in {"response_received", "canonical_completed"}
                or checkpoint["response_kind"] != "dispatch_completed"
                or checkpoint["response_hash"] != response_hash
            ):
                raise InternalSkillCallError("internal_skill_finish_receipt_mismatch")
            payload_bytes = bytes(checkpoint["response_payload"])
            if sha256(payload_bytes).hexdigest() != response_hash:
                raise InternalSkillCallError("internal_skill_finish_receipt_hash_mismatch")
            envelope = json.loads(payload_bytes)
            expected = {
                "resultId": result.result_id,
                "storageType": result.storage_type,
                "storageScope": result.storage_scope,
                "storageRef": result.storage_ref,
                "contentSha256": result.content_sha256,
                "capabilityCallId": child.capability_call_id,
                "parentCapabilityCallId": owner.parent_call_id,
            }
            if (
                any(envelope.get(key) != value for key, value in expected.items())
                or envelope.get("runtimeState") != result.check_result.get("executionStatus")
                or datetime.fromisoformat(envelope["createdAt"]) != result.created_at
                or result.artifact_refs
                or result.scientific_state_versions
                or result.check_result.get("checkScope") != "tool_response_and_storage_integrity"
            ):
                raise InternalSkillCallError("internal_skill_finish_result_identity_conflict")
            status = {
                "succeeded": "succeeded",
                "failed": "failed",
                "unknown": "external_result_unknown",
            }.get(envelope["runtimeState"])
            if status is None:
                raise InternalSkillCallError("internal_skill_finish_state_invalid")
            existing = next(
                (row for row in task.results if row.result_id == result.result_id), None
            )
            if existing is not None:
                if existing != result or child.status.value != status:
                    raise InternalSkillCallError("internal_skill_finish_replay_conflict")
                return existing
            if child.status.value != "running" and not (
                child.status.value == "queued"
                and envelope.get("knownNotSubmitted") is True
                and status == "failed"
            ):
                raise InternalSkillCallError("internal_skill_finish_call_not_running")
            payload = task.model_dump(mode="json", by_alias=True)
            target = next(
                row
                for row in payload["capabilityCalls"]
                if row["capabilityCallId"] == child.capability_call_id
            )
            target.update(status=status, finishedAt=result.created_at.isoformat())
            payload["results"].append(result.model_dump(mode="json", by_alias=True))
            repository._write_locked_task(cursor, raw, ResearchTaskV12.model_validate(payload))
            cursor.execute(
                """UPDATE openair_external_execution_checkpoints
                      SET phase='canonical_completed', canonical_completed_at=clock_timestamp(),
                          updated_at=clock_timestamp()
                    WHERE capability_call_id=%s AND phase='response_received'
                   RETURNING capability_call_id""",
                (child.capability_call_id,),
            )
            if checkpoint["phase"] != "canonical_completed" and cursor.fetchone() is None:
                raise InternalSkillCallError("internal_skill_finish_checkpoint_claim_lost")
            return result

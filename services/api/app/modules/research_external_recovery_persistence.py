"""Persistence helpers for the canonical external-recovery authority."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.execution.research_dispatch_provider import SKILL_FINALIZATION_RUNTIME_FAILED
from app.models.research_external_result import (
    ExplicitRetryCommand,
    ExternalExecutionState,
    FrozenExternalQuery,
    ReconcileExternalResultCommand,
    ReconcileResolution,
)
from app.models.research_orchestration import (
    DispatchStatus,
    ExecutionDecisionOutcome,
    ExecutionDecisionStatus,
    ResearchDesignStatus,
    ResearchTaskV12,
    WorkUnitSourceMode,
    WorkUnitStatus,
)


def _frozen_permission_skills_have_no_action(work: Any, definitions: Any) -> bool:
    """Skill permissions are not execution facts; admit proven reasoning grants."""
    for ref in work.allowed_skill_refs:
        matches = [
            item for item in definitions.get("skills", [])
            if item.get("skillId") == ref.skill_id
            and item.get("definitionVersion") == ref.definition_version
        ]
        if len(matches) != 1:
            return False
        source = matches[0].get("sourceContract") or {}
        runtime = source.get("runtime_contract") or {}
        if (
            runtime.get("kind") != "llm_reasoning"
            or runtime.get("requires_external_action") is not False
        ):
            return False
    return True



def known_failed_non_external_retry_proven(repository: Any, task: Any, row: Any) -> bool:
    """Frozen no-action identity permits restoring a current instance's failed call."""
    if row is None or row.get("status") != "failed" or row.get("external_state") != "known_failed":
        return False
    if (
        row.get("remote_run_id") is not None
        or row.get("external_remote_run_id") is not None
        or row.get("result_storage_ref")
    ):
        return False
    expires = row.get("lease_expires_at")
    if expires is not None and expires > datetime.now(UTC):
        return False
    work = next(
        (item for item in task.work_units if item.work_unit_id == row["work_unit_id"]), None
    )
    decision = next(
        (
            item
            for item in task.execution_decisions
            if item.execution_decision_id == row["execution_decision_id"]
        ),
        None,
    )
    if (
        work is None
        or decision is None
        or work.execution_decision_id != decision.execution_decision_id
        or work.executor_ref.executor_type != "skill"
    ):
        return False
    latest = max(
        (
            item
            for item in task.execution_decisions
            if item.substage_instance_id == work.substage_instance_id
            and item.substage_instance_revision == work.substage_instance_revision
            and item.research_design_version == work.research_design_version
        ),
        key=lambda item: (item.decision_sequence, item.decided_at, item.execution_decision_id),
        default=None,
    )
    if latest is None or latest.execution_decision_id != decision.execution_decision_id:
        return False
    if any(
        item.status in {WorkUnitStatus.QUEUED, WorkUnitStatus.RUNNING, WorkUnitStatus.REVIEWING}
        for item in task.work_units
    ):
        return False
    snapshot = repository.load_registry_snapshot(decision.registry_snapshot_ref.content_hash)
    matches = [
        item
        for item in snapshot.get("definitions", {}).get("skills", [])
        if item.get("skillId") == work.executor_ref.executor_id
        and item.get("definitionVersion") == work.executor_ref.definition_version
    ]
    if len(matches) != 1:
        return False
    runtime = matches[0].get("sourceContract", {}).get("runtime_contract", {})
    if (
        runtime.get("kind") not in {"deterministic", "reasoning_adapter"}
        or runtime.get("requires_external_action") is not False
    ):
        return False
    from app.modules.external_execution_checkpoint import ExternalExecutionCheckpointRepository

    checkpoint = ExternalExecutionCheckpointRepository().get(
        capability_call_id=row["capability_call_id"]
    )
    return bool(
        checkpoint is not None
        and checkpoint.capability_call_id == row["capability_call_id"]
        and checkpoint.execution_kind == "direct_skill_runtime"
        and checkpoint.phase == "prepared"
        and checkpoint.dispatch_id == row["dispatch_id"]
        and checkpoint.response_payload is None
    )


def no_external_action_retry_proven(repository: Any, task: Any, row: Any) -> bool:
    """Prove a lost pure-reasoning response may be independently recomputed.

    This does not resolve the old Call: its unknown fact and evidence survive.
    A live lease, remote execution identity or stored response takes precedence.
    """
    if known_failed_non_external_retry_proven(repository, task, row):
        return True
    if (
        row is None
        or row.get("status") != "submission_unknown"
        or row.get("external_state") != "unknown"
    ):
        return False
    if (
        row.get("remote_run_id") is not None
        or row.get("external_remote_run_id") is not None
        or row.get("result_storage_ref")
    ):
        return False
    expires = row.get("lease_expires_at")
    if expires is not None and expires > datetime.now(UTC):
        return False
    work = next(
        (item for item in task.work_units if item.work_unit_id == row["work_unit_id"]), None,
    )
    decision = next(
        (item for item in task.execution_decisions
         if item.execution_decision_id == row["execution_decision_id"]), None,
    )
    if (
        work is None or decision is None
        or work.execution_decision_id != decision.execution_decision_id
    ):
        return False
    if work.executor_ref.executor_type != "agent":
        return False
    snapshot = repository.load_registry_snapshot(decision.registry_snapshot_ref.content_hash)
    definitions = snapshot.get("definitions") or {}
    matching_definitions = [
        item for item in definitions.get("substages", [])
        if item.get("definitionId") == work.substage_definition_id
        and item.get("definitionVersion") == work.substage_definition_version
    ]
    matching_agents = [
        item for item in definitions.get("agents", [])
        if item.get("agentId") == work.executor_ref.executor_id
        and item.get("definitionVersion") == work.executor_ref.definition_version
    ]
    if len(matching_definitions) != 1 or len(matching_agents) != 1:
        return False
    external = matching_definitions[0].get("externalActionContract") or {}
    agent = matching_agents[0]
    return bool(
        external.get("status") == "known"
        and external.get("requiredActionKinds") == []
        and agent.get("agentKind") == "general_research"
        and (agent.get("budgetPolicy") or {}).get("maxToolCalls") == 0
        and _frozen_permission_skills_have_no_action(work, definitions)
    )


def enqueue_dispatch_continuation(
    cursor: Any, dispatch_id: str, *, rearm_completed: bool = False,
) -> None:
    """Persist the shared system owner for recovery and normal continuation."""
    cursor.execute(
        """INSERT INTO openair_dispatch_continuation (
               dispatch_id, task_id, execution_decision_id, execution_namespace,
               status, last_error_code, completed_at
           )
           SELECT dispatch_id, task_id, execution_decision_id, execution_namespace,
                  'pending', NULL, NULL
             FROM openair_dispatch WHERE dispatch_id = %(dispatch_id)s
           ON CONFLICT (dispatch_id) DO UPDATE
               SET status = 'pending', last_error_code = NULL, completed_at = NULL,
                   next_attempt_at = clock_timestamp(), updated_at = clock_timestamp()
             WHERE %(rearm_completed)s
               AND openair_dispatch_continuation.status = 'completed'""",
        {"dispatch_id": dispatch_id, "rearm_completed": rearm_completed},
    )


def ensure_unknown_recovery_obligation(
    repository: Any,
    cursor: Any,
    *,
    dispatch_id: str,
    error_code: str,
    persistence_error: type[RuntimeError],
) -> str:
    """Atomically bind an unknown Dispatch to its durable system owner."""
    normalized_error = str(error_code or "").strip()
    if not normalized_error:
        raise ValueError("unknown recovery error_code is required")
    cursor.execute(
        """UPDATE openair_external_execution
              SET external_state = 'unknown',
                  failure_code = %(error_code)s,
                  updated_at = clock_timestamp()
            WHERE dispatch_id = %(dispatch_id)s
              AND external_state IN ('unknown', 'submitted', 'running')
        RETURNING *""",
        {"dispatch_id": dispatch_id, "error_code": normalized_error},
    )
    external_execution = cursor.fetchone()
    if external_execution is None:
        raise persistence_error("external_execution_fact_missing")
    evidence_id = repository._insert_unknown_outcome_evidence(
        cursor,
        external_execution=external_execution,
        error_code=normalized_error,
    )
    enqueue_dispatch_continuation(cursor, dispatch_id)
    return evidence_id


def prepare_system_explicit_retry_command(
    repository: Any,
    fence: Any,
    *,
    owner_id: str,
    workspace_id: str,
    error_code: str,
) -> ExplicitRetryCommand | None:
    """Build one bounded retry from known failure or frozen no-action recovery facts."""
    normalized_error = str(error_code or "").strip()
    repository._require_write_enabled()
    repository._require_postgres()
    from app.modules.workbench_user_actions import explicit_retry_reason

    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT d.*, d.status AS dispatch_status,
                          d.remote_run_id AS dispatch_remote_run_id,
                          x.capability_call_id, x.attempt_number,
                          x.remote_run_id AS external_remote_run_id, x.external_state,
                          t.revision AS task_revision, t.payload
                     FROM openair_dispatch d
                     JOIN openair_external_execution x ON x.dispatch_id = d.dispatch_id
                     JOIN openair_tasks t ON t.task_id = x.task_id
                    WHERE d.dispatch_id = %s AND d.task_id = %s
                      AND d.execution_decision_id = %s AND d.work_unit_id = %s
                      AND t.owner_id = %s AND t.workspace_id = %s
                      AND t.status <> 'deleted'
                    FOR UPDATE OF t, d, x""",
                (
                    fence.dispatch_id,
                    fence.task_id,
                    fence.execution_decision_id,
                    fence.work_unit_id,
                    owner_id,
                    workspace_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            task, _raw_payload = repository._task_from_database_payload(
                row["payload"], int(row["task_revision"])
            )
            reasoning_recovery = no_external_action_retry_proven(repository, task, row)
            if not reasoning_recovery and normalized_error != SKILL_FINALIZATION_RUNTIME_FAILED:
                return None
            reason = explicit_retry_reason(
                canonical=task,
                work_unit_id=fence.work_unit_id,
                execution_decision_id=fence.execution_decision_id,
                prior_capability_call_id=str(row["capability_call_id"]),
                prior_attempt_number=int(row["attempt_number"]),
                dispatch_status=str(row["dispatch_status"]),
                external_state=str(row["external_state"]),
                dispatch_remote_run_id=row.get("dispatch_remote_run_id"),
                external_remote_run_id=row.get("external_remote_run_id"),
                last_error_code=str(row.get("last_error_code") or ""),
                proven_no_external_action_retry=reasoning_recovery,
            )
            if reason is None:
                return None
            request_id = (
                f"system-explicit-retry:{fence.dispatch_id}:"
                f"{row['capability_call_id']}:{row['attempt_number']}"
            )
            fingerprint = repository._canonical_fingerprint(
                {
                    "requestId": request_id,
                    "taskId": fence.task_id,
                    "dispatchId": fence.dispatch_id,
                    "capabilityCallId": str(row["capability_call_id"]),
                    "attemptNumber": int(row["attempt_number"]),
                    "taskRevision": int(row["task_revision"]),
                    "reasonCode": reason,
                }
            )
            return ExplicitRetryCommand(
                task_id=fence.task_id,
                work_unit_id=fence.work_unit_id,
                dispatch_id=fence.dispatch_id,
                execution_decision_id=fence.execution_decision_id,
                prior_capability_call_id=str(row["capability_call_id"]),
                prior_attempt_number=int(row["attempt_number"]),
                command_request_id=request_id,
                canonical_payload_fingerprint=fingerprint,
                expected_task_revision=int(row["task_revision"]),
                owner_id=owner_id,
                workspace_id=workspace_id,
                actor_id="system:external-recovery",
                reason_code=reason,
            )


def prepare_system_reconcile_command(
    repository: Any,
    fence: Any,
    *,
    state_error: type[RuntimeError],
) -> ReconcileExternalResultCommand | None:
    """Build one revision-fenced query command for the durable system owner."""
    repository._require_write_enabled()
    repository._require_postgres()
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT d.status AS dispatch_status, x.*, t.revision AS task_revision
                     FROM openair_dispatch d
                     JOIN openair_external_execution x ON x.dispatch_id = d.dispatch_id
                     JOIN openair_tasks t ON t.task_id = x.task_id
                    WHERE d.dispatch_id = %s AND d.task_id = %s
                      AND d.execution_decision_id = %s AND d.work_unit_id = %s
                      AND t.owner_id = %s AND t.workspace_id = %s
                      AND t.status <> 'deleted'
                    FOR UPDATE OF t, d, x""",
                (
                    fence.dispatch_id,
                    fence.task_id,
                    fence.execution_decision_id,
                    fence.work_unit_id,
                    fence.owner_id,
                    fence.workspace_id,
                ),
            )
            row = cursor.fetchone()
            if row is None or str(row["external_state"]) not in {
                ExternalExecutionState.UNKNOWN.value,
                ExternalExecutionState.SUBMITTED.value,
                ExternalExecutionState.RUNNING.value,
            }:
                return None
            if str(row["dispatch_status"]) not in {
                DispatchStatus.SUBMISSION_UNKNOWN.value,
                DispatchStatus.SUBMITTED.value,
            }:
                return None
            cursor.execute(
                """SELECT evidence_id FROM openair_external_evidence
                    WHERE task_id = %s AND execution_decision_id = %s
                      AND work_unit_id = %s AND capability_call_id = %s
                      AND dispatch_id = %s AND attempt_number = %s
                      AND owner_id = %s AND workspace_id = %s
                    ORDER BY evidence_id""",
                (
                    fence.task_id,
                    fence.execution_decision_id,
                    fence.work_unit_id,
                    row["capability_call_id"],
                    fence.dispatch_id,
                    row["attempt_number"],
                    fence.owner_id,
                    fence.workspace_id,
                ),
            )
            evidence_refs = tuple(str(item["evidence_id"]) for item in cursor.fetchall())
            if not evidence_refs:
                raise state_error("system_reconciliation_evidence_missing")
            request_id = f"system-reconcile:{fence.dispatch_id}:{fence.attempt_count}"
            fingerprint = repository._canonical_fingerprint(
                {
                    "requestId": request_id,
                    "taskId": fence.task_id,
                    "dispatchId": fence.dispatch_id,
                    "capabilityCallId": str(row["capability_call_id"]),
                    "attemptNumber": int(row["attempt_number"]),
                    "taskRevision": int(row["task_revision"]),
                    "evidenceRefs": list(evidence_refs),
                }
            )
            return ReconcileExternalResultCommand(
                task_id=fence.task_id,
                work_unit_id=fence.work_unit_id,
                dispatch_id=fence.dispatch_id,
                execution_decision_id=fence.execution_decision_id,
                capability_call_id=str(row["capability_call_id"]),
                attempt_number=int(row["attempt_number"]),
                resolution=ReconcileResolution.QUERY_PROVIDER,
                evidence_refs=evidence_refs,
                command_request_id=request_id,
                canonical_payload_fingerprint=fingerprint,
                expected_task_revision=int(row["task_revision"]),
                owner_id=fence.owner_id,
                workspace_id=fence.workspace_id,
                actor_id="system:external-recovery",
            )


def system_external_recovery_state(
    repository: Any,
    fence: Any,
) -> ExternalExecutionState | None:
    """Return an unresolved state until its canonical Result is materialized."""
    repository._require_postgres()
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT x.external_state, t.payload, t.revision
                     FROM openair_external_execution x
                     JOIN openair_dispatch d ON d.dispatch_id = x.dispatch_id
                     JOIN openair_tasks t ON t.task_id = x.task_id
                    WHERE x.dispatch_id = %s AND x.task_id = %s
                      AND x.execution_decision_id = %s AND x.work_unit_id = %s
                      AND t.owner_id = %s AND t.workspace_id = %s
                      AND t.status <> 'deleted'""",
                (
                    fence.dispatch_id,
                    fence.task_id,
                    fence.execution_decision_id,
                    fence.work_unit_id,
                    fence.owner_id,
                    fence.workspace_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            state = ExternalExecutionState(str(row["external_state"]))
            if state in {
                ExternalExecutionState.UNKNOWN,
                ExternalExecutionState.SUBMITTED,
                ExternalExecutionState.RUNNING,
            }:
                return state
            if state is not ExternalExecutionState.COMPLETED:
                return None
            task, _raw_payload = repository._task_from_database_payload(
                row["payload"], int(row["revision"])
            )
            work_unit = next(
                (item for item in task.work_units if item.work_unit_id == fence.work_unit_id),
                None,
            )
            if work_unit is None or work_unit.status is WorkUnitStatus.COMPLETED:
                return None
            return state


def prepare_reconcile_query(
    repository: Any,
    command: ReconcileExternalResultCommand,
    *,
    task_not_found_error: type[RuntimeError],
    revision_conflict_error: type[RuntimeError],
    recovery_not_found_error: type[RuntimeError],
    state_error: type[RuntimeError],
) -> FrozenExternalQuery:
    """Freeze the persisted execution identity without reloading Task payload."""
    repository._require_write_enabled()
    repository._require_postgres()
    with repository._uow.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT revision FROM openair_tasks
                    WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                      AND status <> 'deleted'
                    FOR UPDATE""",
                (command.task_id, command.owner_id, command.workspace_id),
            )
            task_row = cursor.fetchone()
            if task_row is None:
                raise task_not_found_error("research_task_not_found")
            if int(task_row["revision"]) != command.expected_task_revision:
                raise revision_conflict_error("task_revision_conflict")
            cursor.execute(
                """SELECT d.*, x.capability_call_id, x.attempt_number,
                          x.owner_id, x.workspace_id, x.provider_id, x.adapter,
                          x.provider_idempotency_key, x.receipt,
                          x.remote_run_id AS external_remote_run_id,
                          x.external_state, x.canonical_payload_fingerprint,
                          x.task_id AS external_task_id,
                          x.execution_decision_id AS external_execution_decision_id,
                          x.work_unit_id AS external_work_unit_id
                     FROM openair_dispatch d
                     JOIN openair_external_execution x ON x.dispatch_id = d.dispatch_id
                    WHERE d.task_id = %s AND d.dispatch_id = %s
                    FOR UPDATE OF d, x""",
                (command.task_id, command.dispatch_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise recovery_not_found_error("external_execution_not_found")
            observable_execution = (
                row["external_state"] == ExternalExecutionState.UNKNOWN.value
                and row["status"] == DispatchStatus.SUBMISSION_UNKNOWN.value
            ) or (
                row["external_state"]
                in {
                    ExternalExecutionState.SUBMITTED.value,
                    ExternalExecutionState.RUNNING.value,
                }
                and row["status"] == DispatchStatus.SUBMITTED.value
            )
            if (
                not observable_execution
                or row["external_task_id"] != command.task_id
                or row["execution_decision_id"] != command.execution_decision_id
                or row["external_execution_decision_id"]
                != command.execution_decision_id
                or row["work_unit_id"] != command.work_unit_id
                or row["external_work_unit_id"] != command.work_unit_id
                or row["capability_call_id"] != command.capability_call_id
                or int(row["attempt_number"]) != command.attempt_number
                or row["owner_id"] != command.owner_id
                or row["workspace_id"] != command.workspace_id
                or row["provider_idempotency_key"] != row["idempotency_key"]
            ):
                raise state_error("reconciliation_requires_observable_state")
            repository._require_evidence_refs(cursor, command, command.evidence_refs)
            return FrozenExternalQuery(
                task_id=command.task_id,
                work_unit_id=command.work_unit_id,
                dispatch_id=command.dispatch_id,
                execution_decision_id=command.execution_decision_id,
                capability_call_id=command.capability_call_id,
                attempt_number=command.attempt_number,
                expected_task_revision=int(task_row["revision"]),
                authorization_task_revision=row["authorization_task_revision"],
                owner_id=command.owner_id,
                workspace_id=command.workspace_id,
                provider_id=row["provider_id"],
                adapter=row["adapter"],
                provider_idempotency_key=row["provider_idempotency_key"],
                remote_run_id=row["external_remote_run_id"],
                external_state=ExternalExecutionState(row["external_state"]),
                frozen_work_unit_fingerprint=row["canonical_payload_fingerprint"],
            )


def prepare_continuation_preconditions(
    service: Any,
    fence: Any,
    *,
    owner_id: str,
    workspace_id: str,
    persistence_error: type[RuntimeError],
    revision_conflict_error: type[RuntimeError],
    retryable_error: type[RuntimeError],
) -> tuple[bool, bool]:
    """Resolve stop and semantic-gap state before normal continuation work."""
    try:
        stop_active = service._execution_stop_active(fence)
    except persistence_error as exc:
        if (
            str(exc) == "invalid_research_orchestration_payload"
            and service._system_reconcile_external_result(fence)
        ):
            raise retryable_error("external_recovery_task_payload_invalid") from exc
        raise
    if stop_active:
        return True, False
    try:
        waiting_for_design_confirmation = (
            service._recover_confirmed_semantic_material_gap(
                fence,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        )
    except persistence_error as exc:
        if (
            str(exc) == "invalid_research_orchestration_payload"
            and service._system_reconcile_external_result(fence)
        ):
            raise retryable_error("external_recovery_task_payload_invalid") from exc
        raise
    except revision_conflict_error as exc:
        raise retryable_error("semantic_gap_recovery_revision_changed") from exc
    return False, waiting_for_design_confirmation


def validate_reconcile_row(
    task: Any,
    command: ReconcileExternalResultCommand,
    row: dict[str, Any] | None,
    *,
    not_found_error: type[RuntimeError],
    conflict_error: type[RuntimeError],
    state_error: type[RuntimeError],
) -> None:
    """Validate that a query observation still targets the frozen execution."""
    if row is None:
        raise not_found_error("external_execution_not_found")
    work_unit = next(
        (item for item in task.work_units if item.work_unit_id == command.work_unit_id),
        None,
    )
    capability_call = next(
        (
            item
            for item in task.capability_calls
            if item.capability_call_id == command.capability_call_id
        ),
        None,
    )
    external_state = str(row["external_state"])
    dispatch_status = str(row["status"])
    work_status = work_unit.status if work_unit is not None else None
    call_status = capability_call.status if capability_call is not None else None
    observable_shape = (
        external_state == ExternalExecutionState.UNKNOWN.value
        and dispatch_status == DispatchStatus.SUBMISSION_UNKNOWN.value
        and work_status == WorkUnitStatus.EXTERNAL_RESULT_UNKNOWN
        and call_status is not None
        and call_status.value == "external_result_unknown"
    ) or (
        external_state
        in {
            ExternalExecutionState.SUBMITTED.value,
            ExternalExecutionState.RUNNING.value,
        }
        and dispatch_status == DispatchStatus.SUBMITTED.value
        and work_status == WorkUnitStatus.RUNNING
        and call_status is not None
        and call_status.value == "running"
    )
    if (
        row["execution_decision_id"] != command.execution_decision_id
        or row["work_unit_id"] != command.work_unit_id
        or row["capability_call_id"] != command.capability_call_id
        or int(row["attempt_number"]) != command.attempt_number
        or row["owner_id"] != command.owner_id
        or row["workspace_id"] != command.workspace_id
        or row["provider_idempotency_key"] != row["idempotency_key"]
    ):
        raise conflict_error("reconciliation_lineage_mismatch")
    if (
        not observable_shape
        or work_unit is None
        or capability_call is None
        or capability_call.work_unit_id != command.work_unit_id
        or capability_call.attempt_number != command.attempt_number
    ):
        raise state_error("reconciliation_requires_observable_state")


__all__ = [
    "enqueue_dispatch_continuation",
    "ensure_unknown_recovery_obligation",
    "prepare_continuation_preconditions",
    "prepare_reconcile_query",
    "prepare_system_explicit_retry_command",
    "prepare_system_reconcile_command",
    "system_external_recovery_state",
    "validate_reconcile_row",
]


def validate_known_failed_safe_retry_authorization(
    task: ResearchTaskV12,
    dispatch: dict[str, Any],
    *,
    authorization_error: type[RuntimeError],
    proven_no_external_action_retry: bool = False,
):
    """Authorize a bounded system retry without requiring an open DISPATCHED Decision."""
    if task.status.value != "running":
        raise authorization_error("task_not_running")
    decision = next(
        (
            row
            for row in task.execution_decisions
            if row.execution_decision_id == dispatch["execution_decision_id"]
        ),
        None,
    )
    work_unit = next(
        (row for row in task.work_units if row.work_unit_id == dispatch["work_unit_id"]),
        None,
    )
    if decision is None or work_unit is None:
        raise authorization_error("dispatch_lineage_mismatch")
    latest = max(
        [
            row
            for row in task.execution_decisions
            if not proven_no_external_action_retry
            or (
                row.substage_instance_id == decision.substage_instance_id
                and row.substage_instance_revision == decision.substage_instance_revision
                and row.research_design_version == decision.research_design_version
            )
        ],
        key=lambda row: (row.decision_sequence, row.decided_at, row.execution_decision_id),
        default=None,
    )
    if latest is None or latest.execution_decision_id != decision.execution_decision_id:
        raise authorization_error("decision_not_latest")
    active_ref = task.active_research_design_ref
    design = next(
        (
            row
            for row in task.research_design_versions
            if active_ref is not None
            and row.research_design_id == active_ref.research_design_id
            and row.version == active_ref.version
        ),
        None,
    )
    if (
        design is None
        or design.status != ResearchDesignStatus.CONFIRMED
        or decision.research_design_version != design.version
    ):
        raise authorization_error("design_not_active_confirmed")
    if not any(
        row.substage_instance_id == decision.substage_instance_id
        and row.instance_revision == decision.substage_instance_revision
        for row in design.instances
    ):
        raise authorization_error("instance_lineage_mismatch")
    active_same_selection = [
        row
        for row in task.execution_decisions
        if row.selection_request_id == decision.selection_request_id
        and row.status in {ExecutionDecisionStatus.SELECTED, ExecutionDecisionStatus.DISPATCHED}
    ]
    if decision.status is ExecutionDecisionStatus.DISPATCHED:
        if (
            len(active_same_selection) != 1
            or active_same_selection[0].execution_decision_id != decision.execution_decision_id
        ):
            raise authorization_error("decision_not_unique_active")
    elif decision.status is ExecutionDecisionStatus.CLOSED and (
        decision.outcome is ExecutionDecisionOutcome.BLOCKED
        or (
            proven_no_external_action_retry
            and decision.outcome is ExecutionDecisionOutcome.SUCCEEDED
        )
    ):
        if active_same_selection:
            raise authorization_error("decision_not_unique_active")
    else:
        raise authorization_error("decision_not_retryable")
    if (
        work_unit.source_mode != WorkUnitSourceMode.LIVE
        or not work_unit.dispatchable
        or work_unit.status != WorkUnitStatus.QUEUED
        or work_unit.execution_decision_id != decision.execution_decision_id
        or work_unit.research_design_version != decision.research_design_version
        or work_unit.substage_instance_id != decision.substage_instance_id
        or work_unit.substage_instance_revision != decision.substage_instance_revision
        or work_unit.idempotency_key != dispatch["idempotency_key"]
    ):
        raise authorization_error("work_unit_not_dispatchable")
    return work_unit

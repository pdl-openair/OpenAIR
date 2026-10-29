"""Transactional persistence boundary for canonical Task lifecycle changes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.modules.research_progress_projection import stamp_progress_projection
from app.modules.research_task_lifecycle import (
    ResearchStartLifecycleFact,
    ResearchTaskLifecycleFact,
    TaskLifecycleTransition,
    reconcile_task_lifecycle,
)

_CANONICAL_TASK_STATUSES = frozenset(
    {
        "draft",
        "not_started",
        "running",
        "awaiting_confirmation",
        "completed",
        "failed",
        "cancelled",
    }
)


class TaskLifecycleWriteConflictError(RuntimeError):
    """The locked Task no longer accepts the lifecycle conclusion."""


def apply_research_start_lifecycle_fact_locked(
    cur: Any,
    *,
    session: Mapping[str, Any],
    fact: ResearchStartLifecycleFact,
) -> bool:
    """Atomically apply one Research Start fact through the lifecycle boundary.

    The caller owns the surrounding Research Start transaction.  This function
    takes the Task row lock, derives the canonical transition, and advances the
    Task row and its read projection in the same revision.
    """

    cur.execute(
        """SELECT payload, revision FROM openair_tasks
             WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
               AND status <> 'deleted'
             FOR UPDATE""",
        (session["reserved_task_id"], session["owner_id"], session["workspace_id"]),
    )
    row = cur.fetchone()
    if row is None:
        return False
    payload = _payload_from_row(row)
    if payload is None:
        return False
    return commit_task_lifecycle_mutation_locked(
        cur,
        task_row=row,
        payload=payload,
        task_id=str(session["reserved_task_id"]),
        owner_id=str(session["owner_id"]),
        workspace_id=str(session["workspace_id"]),
        fact=fact,
    )


def commit_task_lifecycle_mutation_locked(
    cur: Any,
    *,
    task_row: Mapping[str, Any],
    payload: dict[str, Any],
    task_id: str,
    owner_id: str,
    workspace_id: str,
    fact: ResearchTaskLifecycleFact | None = None,
    fact_task: Mapping[str, Any] | None = None,
    task_column_updates: Mapping[str, Any] | None = None,
) -> bool:
    """Persist one locked Task mutation through the lifecycle boundary.

    Callers may change durable facts in ``payload``.  They cannot choose the
    canonical Task status: without a lifecycle fact the locked database status
    is preserved; with one, the common reducer derives the only allowed next
    state.  A rejected fact makes no Task write.
    """

    current_status = str(task_row.get("status") or payload.get("status") or "not_started")
    payload["status"] = current_status
    lifecycle_basis = dict(fact_task or payload)
    lifecycle_basis["status"] = current_status
    transition = reconcile_task_lifecycle(lifecycle_basis, fact=fact) if fact is not None else None
    if fact is not None and transition is None:
        return False

    expected_revision = int(task_row["revision"])
    next_revision = expected_revision + 1
    next_status = transition.task_status if transition is not None else current_status
    payload["revision"] = next_revision
    payload["status"] = next_status
    stamp_progress_projection(payload, next_revision=next_revision)
    if transition is not None:
        _set_progress_status(payload, transition.progress_status)

    if transition is None or transition.expected_database_status is None:
        status_guard = "status <> 'deleted'"
        status_guard_params: tuple[Any, ...] = ()
    else:
        status_guard = "status = %s"
        status_guard_params = (transition.expected_database_status,)
    allowed_columns = ("current_stage", "title", "domain")
    requested_columns = task_column_updates or {}
    unknown_columns = set(requested_columns).difference(allowed_columns)
    if unknown_columns:
        raise ValueError(f"unsupported Task column update: {sorted(unknown_columns)!r}")
    column_assignments = ["status = %s"]
    column_params: list[Any] = [next_status]
    # Keep the established scoped Task mutation parameter order when callers
    # also change list-card columns. It matters to the compatibility harness,
    # while the lifecycle boundary remains the only writer of ``status``.
    for column in allowed_columns:
        if column in requested_columns:
            column_assignments.append(f"{column} = %s")
            column_params.append(requested_columns[column])
    column_assignments.extend(
        [
            "revision = %s",
            "updated_at = clock_timestamp()",
            "payload = %s::jsonb",
        ]
    )
    column_params.extend([next_revision, json.dumps(payload, ensure_ascii=False)])
    cur.execute(
        f"""UPDATE openair_tasks
              SET {", ".join(column_assignments)}
            WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
              AND revision = %s AND {status_guard}""",
        (
            *column_params,
            task_id,
            owner_id,
            workspace_id,
            expected_revision,
            *status_guard_params,
        ),
    )
    if cur.rowcount != 1:
        raise TaskLifecycleWriteConflictError("task_lifecycle_write_conflict")
    return True


def commit_reconciled_task_projection_locked(
    cur: Any,
    *,
    task_row: Mapping[str, Any],
    payload: dict[str, Any],
    task_id: str,
    owner_id: str,
    workspace_id: str,
    reconciled_status: str,
    reconciled_progress_status: str | None = None,
    task_column_updates: Mapping[str, Any] | None = None,
) -> bool:
    """Write an already-reconciled runtime projection through the one boundary.

    ``reconcile_task_lifecycle_from_runtime`` remains the authoritative runtime
    reducer because it needs the frozen ResearchDesign and Registry snapshot.
    It may compute a Task conclusion, but it never writes the Task row itself.
    This adapter accepts only that public conclusion and delegates the actual
    revision/CAS/status write to the common lifecycle boundary.
    """

    if reconciled_status not in _CANONICAL_TASK_STATUSES:
        raise ValueError("invalid reconciled Task status")
    current_status = str(task_row.get("status") or "")
    if current_status not in _CANONICAL_TASK_STATUSES:
        raise ValueError("invalid locked Task status")
    transition = TaskLifecycleTransition(
        task_status=reconciled_status,
        progress_status=reconciled_progress_status or reconciled_status,
        expected_database_status=current_status,
    )
    return _commit_task_lifecycle_transition_locked(
        cur,
        task_row=task_row,
        payload=payload,
        task_id=task_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
        transition=transition,
        task_column_updates=task_column_updates,
    )


def _commit_task_lifecycle_transition_locked(
    cur: Any,
    *,
    task_row: Mapping[str, Any],
    payload: dict[str, Any],
    task_id: str,
    owner_id: str,
    workspace_id: str,
    transition: TaskLifecycleTransition,
    task_column_updates: Mapping[str, Any] | None = None,
) -> bool:
    """Persist one transition that has already been derived by a lifecycle reducer."""

    expected_revision = int(task_row["revision"])
    next_revision = expected_revision + 1
    payload["revision"] = next_revision
    payload["status"] = transition.task_status
    stamp_progress_projection(payload, next_revision=next_revision)
    _set_progress_status(payload, transition.progress_status)

    status_guard = "status = %s"
    status_guard_params: tuple[Any, ...] = (transition.expected_database_status,)
    allowed_columns = ("current_stage", "title", "domain")
    requested_columns = task_column_updates or {}
    unknown_columns = set(requested_columns).difference(allowed_columns)
    if unknown_columns:
        raise ValueError(f"unsupported Task column update: {sorted(unknown_columns)!r}")
    column_assignments = ["status = %s"]
    column_params: list[Any] = [transition.task_status]
    for column in allowed_columns:
        if column in requested_columns:
            column_assignments.append(f"{column} = %s")
            column_params.append(requested_columns[column])
    column_assignments.extend(
        [
            "revision = %s",
            "updated_at = clock_timestamp()",
            "payload = %s::jsonb",
        ]
    )
    column_params.extend([next_revision, json.dumps(payload, ensure_ascii=False)])
    cur.execute(
        f"""UPDATE openair_tasks
              SET {", ".join(column_assignments)}
            WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
              AND revision = %s AND {status_guard}""",
        (
            *column_params,
            task_id,
            owner_id,
            workspace_id,
            expected_revision,
            *status_guard_params,
        ),
    )
    if cur.rowcount != 1:
        raise TaskLifecycleWriteConflictError("task_lifecycle_write_conflict")
    return True


def _payload_from_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_payload = row.get("payload")
    if isinstance(raw_payload, str):
        raw_payload = json.loads(raw_payload)
    if not isinstance(raw_payload, dict):
        return None
    return deepcopy(raw_payload)


def _set_progress_status(payload: dict[str, Any], status: str) -> None:
    projection = payload.get("progressProjection")
    if not isinstance(projection, dict):
        return
    task_progress = projection.get("taskProgress")
    if isinstance(task_progress, dict):
        task_progress["status"] = status

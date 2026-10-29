"""User-authorized regeneration of a research plan on the same study.

The stored intent decides the path. A selected research flow is replayed by
the durable worker; otherwise the worker runs its planner path again. This
command does not copy a previous attempt forward, and it does not replay an
unconfirmed provider submission.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from app.modules.research_start_attempt_lease import REQUEST_COLUMNS, SESSION_COLUMNS
from app.modules.research_start_errors import ResearchStartError, TerminalStateError
from app.modules.research_start_projection import _mapping, _row
from app.modules.research_start_repository import ResearchStartRepository
from app.modules.research_task_lifecycle import ResearchStartLifecycleFact
from app.modules.research_task_lifecycle_repository import (
    TaskLifecycleWriteConflictError,
    apply_research_start_lifecycle_fact_locked,
)


def task_has_research_design(task: Mapping[str, Any]) -> bool:
    """True when a research plan already exists on the task."""

    return bool(
        task.get("activeResearchDesignRef")
        or task.get("pendingResearchDesignRef")
        or task.get("creationPlan")
        or (task.get("researchDesignVersions") or [])
    )


def regenerate_failed_research_plan(
    *,
    owner_id: str,
    workspace_id: str,
    task_id: str,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Queue a fresh plan generation for one failed study, or explain why not."""

    try:
        return _regenerate_locked(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
            connection_factory=connection_factory,
        )
    except ResearchStartError as exc:
        return {"applied": False, "reason": exc.code}


def _regenerate_locked(
    *,
    owner_id: str,
    workspace_id: str,
    task_id: str,
    connection_factory: Callable[[], Any] | None,
) -> dict[str, Any]:
    repo = ResearchStartRepository(connection_factory=connection_factory)
    with repo._connect() as conn:
        with conn.cursor() as cur:
            session = _lock_session(
                cur, owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
            )
            if session is None:
                return {"applied": False, "reason": "session_not_found"}
            session_id = str(session["session_id"])
            open_request = _open_request(cur, session)
            if session["state"] == "running" and open_request is not None:
                return {
                    "applied": True,
                    "reason": "already_regenerating",
                    "sessionId": session_id,
                }
            if session["state"] != "failed":
                return {
                    "applied": False,
                    "reason": str(session["state"]),
                    "sessionId": session_id,
                }
            if task_has_research_design(_locked_task_payload(cur, session)):
                return {"applied": False, "reason": "design_exists", "sessionId": session_id}
            if not _authorize_task_regeneration(cur, session):
                return {
                    "applied": False,
                    "reason": "task_not_regenerable",
                    "sessionId": session_id,
                }
            _reopen_failed_session(cur, session)
            if open_request is not None:
                return {
                    "applied": True,
                    "reason": "already_regenerating",
                    "sessionId": session_id,
                }
            prior = _latest_request(cur, session)
            if prior is None:
                raise TerminalStateError()
            repo._enforce_durable_admission(cur, owner_id, workspace_id)
            request = repo._insert_continuation_request_locked(
                cur, session=session, prior_request=prior
            )
            return {
                "applied": True,
                "reason": "queued",
                "sessionId": session_id,
                "requestId": request["request_id"],
            }


def _lock_session(
    cur: Any, *, owner_id: str, workspace_id: str, task_id: str
) -> dict[str, Any] | None:
    cur.execute(
        f"""SELECT {SESSION_COLUMNS} FROM research_start_sessions
             WHERE reserved_task_id = %s AND owner_id = %s AND workspace_id = %s
             FOR UPDATE""",
        (task_id, owner_id, workspace_id),
    )
    return _row(cur.fetchone())


def _open_request(cur: Any, session: Mapping[str, Any]) -> dict[str, Any] | None:
    cur.execute(
        f"""SELECT {REQUEST_COLUMNS} FROM research_start_requests
             WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
               AND state IN ('queued', 'leased')
             ORDER BY created_at, request_id
             LIMIT 1""",
        (session["session_id"], session["owner_id"], session["workspace_id"]),
    )
    return _row(cur.fetchone())


def _latest_request(cur: Any, session: Mapping[str, Any]) -> dict[str, Any] | None:
    cur.execute(
        f"""SELECT {REQUEST_COLUMNS} FROM research_start_requests
             WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
             ORDER BY created_at DESC, request_id DESC
             LIMIT 1 FOR UPDATE""",
        (session["session_id"], session["owner_id"], session["workspace_id"]),
    )
    return _row(cur.fetchone())


def _locked_task_payload(cur: Any, session: Mapping[str, Any]) -> Mapping[str, Any]:
    cur.execute(
        """SELECT payload FROM openair_tasks
            WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
              AND status <> 'deleted'
            FOR UPDATE""",
        (session["reserved_task_id"], session["owner_id"], session["workspace_id"]),
    )
    row = cur.fetchone()
    if not isinstance(row, Mapping):
        raise TerminalStateError()
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    parsed = _mapping(payload)
    if parsed is None:
        raise TerminalStateError()
    return parsed


def _authorize_task_regeneration(cur: Any, session: Mapping[str, Any]) -> bool:
    try:
        return bool(
            apply_research_start_lifecycle_fact_locked(
                cur,
                session=session,
                fact=ResearchStartLifecycleFact.PLAN_REGENERATION_AUTHORIZED,
            )
        )
    except TaskLifecycleWriteConflictError as exc:
        raise TerminalStateError() from exc


def _reopen_failed_session(cur: Any, session: Mapping[str, Any]) -> None:
    cur.execute(
        """UPDATE research_start_sessions
              SET state = 'running', result_payload = NULL, user_message = NULL,
                  failure_code = NULL,
                  updated_at = clock_timestamp()
            WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
              AND state = 'failed'""",
        (session["session_id"], session["owner_id"], session["workspace_id"]),
    )
    if cur.rowcount != 1:
        raise TerminalStateError()

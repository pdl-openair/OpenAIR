"""Atomic Research Start queue reconciliation and heavy admission policy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

ADMISSION_LOCK_KEY = "research-start-durable-admission-v1"
STALE_RUNTIME_SOURCE_FAILURE_CODE = "runtime_source_unclaimable"
STALE_RUNTIME_SOURCE_USER_MESSAGE = (
    "这条研究准备请求来自已停用的运行版本，系统已安全结束旧队列占位。请重新提交研究任务。"
)


class AdmissionRejection(NamedTuple):
    position: int
    scope: str


def _row(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    return dict(value)


def _lock_next_stale_session(cur: Any, runtime_source_version: str) -> dict[str, Any] | None:
    cur.execute(
        """SELECT s.session_id, s.owner_id, s.workspace_id, s.reserved_task_id
             FROM research_start_sessions s
            WHERE s.state = 'running'
              AND NOT EXISTS (
                  SELECT 1 FROM openair_tasks t
                   WHERE t.task_id = s.reserved_task_id
                     AND t.owner_id = s.owner_id
                     AND t.workspace_id = s.workspace_id
              )
              AND EXISTS (
                  SELECT 1 FROM research_start_requests stale
                   WHERE stale.session_id = s.session_id
                     AND stale.owner_id = s.owner_id
                     AND stale.workspace_id = s.workspace_id
                     AND stale.state = 'queued'
                     AND stale.runtime_source_version IS DISTINCT FROM %s
                     AND NOT EXISTS (
                         SELECT 1 FROM research_start_attempts durable_attempt
                          WHERE durable_attempt.request_id = stale.request_id
                            AND durable_attempt.state IN ('started', 'succeeded', 'unknown')
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM openair_provider_http_attempts physical
                          WHERE physical.parent_kind = 'research_start'
                            AND physical.parent_id = stale.request_id
                            AND physical.evidence_kind = 'provider_submission'
                            AND physical.transport_outcome <> 'transport_not_submitted'
                     )
              )
            ORDER BY s.created_at, s.session_id
            FOR UPDATE OF s SKIP LOCKED LIMIT 1""",
        (runtime_source_version,),
    )
    return _row(cur.fetchone())


def _lock_stale_requests(
    cur: Any,
    session: Mapping[str, Any],
    runtime_source_version: str,
) -> tuple[str, ...]:
    cur.execute(
        """SELECT request_id FROM research_start_requests
             WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
               AND state = 'queued'
               AND runtime_source_version IS DISTINCT FROM %s
               AND NOT EXISTS (
                   SELECT 1 FROM research_start_attempts durable_attempt
                    WHERE durable_attempt.request_id = research_start_requests.request_id
                      AND durable_attempt.state IN ('started', 'succeeded', 'unknown')
               )
               AND NOT EXISTS (
                   SELECT 1 FROM openair_provider_http_attempts physical
                    WHERE physical.parent_kind = 'research_start'
                      AND physical.parent_id = research_start_requests.request_id
                      AND physical.evidence_kind = 'provider_submission'
                      AND physical.transport_outcome <> 'transport_not_submitted'
               )
             ORDER BY created_at, request_id
             FOR UPDATE""",
        (
            session["session_id"],
            session["owner_id"],
            session["workspace_id"],
            runtime_source_version,
        ),
    )
    return tuple(str(row["request_id"]) for row in cur.fetchall())


def _settle_stale_requests(
    cur: Any,
    session: Mapping[str, Any],
    runtime_source_version: str,
    request_ids: tuple[str, ...],
) -> int:
    cur.execute(
        """UPDATE research_start_requests
              SET state = %s, lease_owner = NULL, lease_expires_at = NULL,
                  result_payload = NULL, updated_at = clock_timestamp()
            WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
              AND state = 'queued'
              AND runtime_source_version IS DISTINCT FROM %s
              AND request_id = ANY(%s)""",
        (
            "failed",
            session["session_id"],
            session["owner_id"],
            session["workspace_id"],
            runtime_source_version,
            list(request_ids),
        ),
    )
    return int(cur.rowcount)


def _settle_session_if_orphaned(
    cur: Any,
    session: Mapping[str, Any],
) -> None:
    cur.execute(
        """SELECT EXISTS (
                   SELECT 1 FROM research_start_requests active
                    WHERE active.session_id = %s
                      AND active.owner_id = %s
                      AND active.workspace_id = %s
                      AND active.state IN ('queued', 'leased')
               ) AS has_active_request""",
        (session["session_id"], session["owner_id"], session["workspace_id"]),
    )
    active = _row(cur.fetchone()) or {}
    if active.get("has_active_request") is True:
        return

    cur.execute(
        """UPDATE research_start_sessions
              SET state = 'failed', result_payload = NULL, user_message = %s,
                  failure_code = %s, updated_at = clock_timestamp()
            WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
              AND state = 'running'""",
        (
            STALE_RUNTIME_SOURCE_USER_MESSAGE,
            STALE_RUNTIME_SOURCE_FAILURE_CODE,
            session["session_id"],
            session["owner_id"],
            session["workspace_id"],
        ),
    )
    if cur.rowcount != 1:
        raise RuntimeError("stale research-start session lost its reconciliation fence")


def reconcile_unowned_unclaimable_queue(cur: Any, runtime_source_version: str) -> int:
    """Terminalize stale queue slots without erasing execution facts.

    The caller owns the global admission advisory lock. Sessions are locked
    before requests, matching claim/finalize lock order. Reserved Tasks, active
    or durable attempts, and provider submission facts remain admission-visible
    owner boundaries.
    """

    reconciled = 0
    while session := _lock_next_stale_session(cur, runtime_source_version):
        stale_request_ids = _lock_stale_requests(cur, session, runtime_source_version)
        if not stale_request_ids:
            continue
        settled = _settle_stale_requests(
            cur,
            session,
            runtime_source_version,
            stale_request_ids,
        )
        if settled != len(stale_request_ids):
            raise RuntimeError("stale research-start requests changed during reconciliation")
        _settle_session_if_orphaned(cur, session)
        reconciled += settled
    return reconciled


def enforce_durable_admission(
    cur: Any,
    *,
    owner_id: str,
    workspace_id: str,
    runtime_source_version: str,
    enabled: bool,
    global_limit: int,
    scoped_limit: int,
) -> AdmissionRejection | None:
    """Reconcile, then count and decide under the existing global lock."""

    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (ADMISSION_LOCK_KEY,),
    )
    reconcile_unowned_unclaimable_queue(cur, runtime_source_version)
    if not enabled:
        return None
    cur.execute(
        """SELECT
                   COUNT(*) FILTER (WHERE state IN ('queued', 'leased')) AS global_backlog,
                   COUNT(*) FILTER (
                       WHERE state IN ('queued', 'leased')
                         AND owner_id = %s AND workspace_id = %s
                   ) AS scoped_backlog
              FROM research_start_requests""",
        (owner_id, workspace_id),
    )
    counts = _row(cur.fetchone()) or {}
    global_backlog = int(counts.get("global_backlog") or 0)
    scoped_backlog = int(counts.get("scoped_backlog") or 0)
    if global_backlog >= max(1, int(global_limit)):
        return AdmissionRejection(global_backlog + 1, "global")
    if scoped_backlog >= max(1, int(scoped_limit)):
        return AdmissionRejection(scoped_backlog + 1, "owner_workspace")
    return None


__all__ = ["AdmissionRejection", "enforce_durable_admission", "reconcile_unowned_unclaimable_queue"]

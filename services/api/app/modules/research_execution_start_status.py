"""Canonical, revision-bound authority for initial Research execution Start."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, ContextManager, Protocol

from pydantic import ValidationError

from app.config import settings
from app.models.research_execution_start_status import (
    ResearchExecutionStartRequestStatusOut,
    ResearchExecutionStartStatusOut,
)
from app.models.research_orchestration import (
    ResearchDesignStatus,
    ResearchTaskV12,
    TaskStatus,
)
from app.modules.research_blocker_semantics import needs_user_input


class ResearchExecutionStartTaskNotFound(RuntimeError):
    pass


class ResearchExecutionInitialStartDenied(RuntimeError):
    """Final write-boundary rejection from the canonical initial-start policy."""

    def __init__(self, start_status: ResearchExecutionStartStatusOut) -> None:
        self.start_status = start_status
        code = (
            start_status.blockingReasonCode
            or start_status.failureReasonCode
            or "initial_execution_start_not_allowed"
        )
        super().__init__(code)


class SettingsLike(Protocol):
    persistence_backend: str
    postgres_host: str
    postgres_port: int
    postgres_database: str
    postgres_user: str
    postgres_password: str


ConnectionFactory = Callable[[], ContextManager[Any]]

_SYSTEM_BLOCKER_SUMMARY = (
    "系统尚不能安全启动该研究。当前任务版本存在系统侧启动阻塞；"
    "需要由系统修复或形成新的合法任务版本后再启动。"
)
_USER_BLOCKER_SUMMARY = (
    "当前任务版本还需要用户提供或确认必要信息；完成后系统会基于新的任务事实重新判断是否可启动。"
)
_UNKNOWN_START_SUMMARY = (
    "系统暂时无法确认当前任务版本是否可以安全启动，因此不会允许重复启动。"
)
_CURRENT_DESIGN_REQUIRED_SUMMARY = (
    "当前任务版本没有可启动的已确认研究方案；请先确认当前研究方案。"
)
_TERMINAL_START_SUMMARY = "当前任务已结束，不能再次开始研究。"


def _coerce_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _revision(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _parse_task(
    payload: dict[str, Any] | str,
    database_revision: int,
    task_id: str,
) -> ResearchTaskV12 | None:
    raw = _coerce_json_object(payload)
    if raw is None or raw.get("revision") != database_revision:
        return None
    try:
        aliases = {
            field.alias or name
            for name, field in ResearchTaskV12.model_fields.items()
        }
        task = ResearchTaskV12.model_validate({alias: raw[alias] for alias in aliases})
        return task if task.task_id == task_id else None
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


def _current_confirmed_design(task: ResearchTaskV12):
    if not task.research_design_versions:
        return None, "current_confirmed_design_required"
    latest = max(task.research_design_versions, key=lambda row: row.version)
    if latest.status is not ResearchDesignStatus.CONFIRMED:
        return None, "current_confirmed_design_required"
    active = task.active_research_design_ref
    if active is None:
        return None, "current_design_activation_missing"
    if (
        active.research_design_id != latest.research_design_id
        or active.version != latest.version
    ):
        return None, "current_design_activation_mismatch"
    design = next(
        (
            row
            for row in task.research_design_versions
            if row.research_design_id == active.research_design_id
            and row.version == active.version
            and row.status is ResearchDesignStatus.CONFIRMED
        ),
        None,
    )
    return (
        (design, "")
        if design is not None
        else (None, "current_design_activation_mismatch")
    )


def _blocker_policy(reason_code: str) -> tuple[str, bool, str]:
    if reason_code == "current_confirmed_design_required":
        return "USER", True, _CURRENT_DESIGN_REQUIRED_SUMMARY
    if reason_code == "task_terminal":
        return "TERMINAL", False, _TERMINAL_START_SUMMARY
    if needs_user_input(reason_code):
        return "USER", True, _USER_BLOCKER_SUMMARY
    # Unknown/non-user reasons fail closed as SYSTEM.  This backend rule is the
    # responsibility authority; frontend reason-code lists are presentation only.
    return "SYSTEM", False, _SYSTEM_BLOCKER_SUMMARY


def _blocked_status(
    *,
    task_id: str,
    task_revision: int,
    blocker_revision: int,
    reason_code: str,
) -> ResearchExecutionStartStatusOut:
    responsibility, retryable, summary = _blocker_policy(reason_code)
    return ResearchExecutionStartStatusOut(
        taskId=task_id,
        state="blocked",
        taskRevision=task_revision,
        startAllowed=False,
        responsibility=responsibility,
        retryable=retryable,
        reasonSummary=summary,
        blockerRevision=blocker_revision,
        blockingReasonCode=reason_code,
    )


def _unknown_status(*, task_id: str, task_revision: int) -> ResearchExecutionStartStatusOut:
    return ResearchExecutionStartStatusOut(
        taskId=task_id,
        state="unknown",
        taskRevision=task_revision,
        startAllowed=False,
        responsibility="SYSTEM",
        retryable=False,
        reasonSummary=_UNKNOWN_START_SUMMARY,
    )


def _unblocked_status(
    *,
    task_id: str,
    task_revision: int,
    start_allowed: bool,
    state: str,
    execution_decision_id: str | None = None,
    failure_reason_code: str | None = None,
) -> ResearchExecutionStartStatusOut:
    if state in {"accepted", "running", "committed"}:
        start_allowed = False

    return ResearchExecutionStartStatusOut(
        taskId=task_id,
        state=state,
        taskRevision=task_revision,
        startAllowed=start_allowed,
        responsibility="NONE",
        retryable=bool(start_allowed),
        executionDecisionId=execution_decision_id,
        failureReasonCode=failure_reason_code,
    )


def _queue_blocker(
    result_payload: Any,
    *,
    expected_revision: Any,
) -> tuple[str, int | None] | None:
    payload = _coerce_json_object(result_payload)
    if payload is None:
        return None
    reason = str(payload.get("reasonCode") or "").strip()
    facts = payload.get("facts")
    blocker_revision = (
        _revision(facts.get("taskRevision"))
        if isinstance(facts, Mapping)
        else None
    )
    if blocker_revision is None:
        blocker_revision = _revision(expected_revision)
    return (reason, blocker_revision) if reason else None


def _parse_attempt_blocker(outcome: Any) -> tuple[str, int | None] | None:
    text = str(outcome or "").strip()
    if not text.startswith("blocked:"):
        return None
    payload_text = text.partition(":")[2].strip()
    if not payload_text.startswith("{"):
        return (payload_text, None) if payload_text else None
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    reason = str(payload.get("reasonCode") or "").strip()
    facts = payload.get("facts")
    blocker_revision = (
        _revision(facts.get("taskRevision"))
        if isinstance(facts, Mapping)
        else None
    )
    return (reason, blocker_revision) if reason else None


def _current_status_from_row(*, task_id: str, row: Mapping[str, Any]) -> ResearchExecutionStartStatusOut:
    task_revision = int(row["revision"])
    queue_state = str(row.get("current_job_state") or "").strip()
    task = _parse_task(row["payload"], task_revision, task_id)
    if task is None:
        return _unknown_status(task_id=task_id, task_revision=task_revision)
    current_design, design_blocker = _current_confirmed_design(task)
    if current_design is None:
        return _blocked_status(
            task_id=task_id,
            task_revision=task_revision,
            blocker_revision=task_revision,
            reason_code=design_blocker,
        )

    current_decisions = [
        decision
        for decision in task.execution_decisions
        if decision.research_design_version == current_design.version
    ]
    if current_decisions:
        return _unblocked_status(
            task_id=task_id,
            task_revision=task_revision,
            start_allowed=False,
            state="committed",
            execution_decision_id=current_decisions[-1].execution_decision_id,
        )

    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return _blocked_status(
            task_id=task_id,
            task_revision=task_revision,
            blocker_revision=task_revision,
            reason_code="task_terminal",
        )
    if task.status is not TaskStatus.NOT_STARTED:
        return _unknown_status(task_id=task_id, task_revision=task_revision)

    if not queue_state:
        return _unblocked_status(
            task_id=task_id,
            task_revision=task_revision,
            start_allowed=True,
            state="not_committed",
        )

    if queue_state == "blocked":
        blocker = _queue_blocker(
            row.get("current_job_result_payload"),
            expected_revision=row.get("current_job_expected_revision"),
        )
        if blocker is None:
            return _unknown_status(task_id=task_id, task_revision=task_revision)
        reason, blocker_revision = blocker
        if blocker_revision != task_revision:
            # Never relabel a stale blocker as the current revision.
            return _unknown_status(task_id=task_id, task_revision=task_revision)
        return _blocked_status(
            task_id=task_id,
            task_revision=task_revision,
            blocker_revision=blocker_revision,
            reason_code=reason,
        )

    if queue_state == "queued":
        return _unblocked_status(
            task_id=task_id,
            task_revision=task_revision,
            start_allowed=False,
            state="accepted",
        )
    if queue_state == "leased":
        return _unblocked_status(
            task_id=task_id,
            task_revision=task_revision,
            start_allowed=False,
            state="running",
        )
    if queue_state == "failed":
        return _unblocked_status(
            task_id=task_id,
            task_revision=task_revision,
            start_allowed=True,
            state="failed",
            failure_reason_code=(
                str(row.get("current_job_last_error_code") or "").strip()
                or "execution_selection_failed"
            ),
        )
    if queue_state == "external_result_unknown":
        return _unblocked_status(
            task_id=task_id,
            task_revision=task_revision,
            start_allowed=True,
            state="failed",
            failure_reason_code=(
                str(row.get("current_job_last_error_code") or "").strip()
                or "execution_selection_system_failure"
            ),
        )
    if queue_state == "succeeded":
        payload = _coerce_json_object(row.get("current_job_result_payload"))
        decision_id = (
            str(payload.get("executionDecisionId") or "").strip()
            if payload is not None
            else ""
        )
        if not decision_id:
            return _unknown_status(task_id=task_id, task_revision=task_revision)
        return _unblocked_status(
            task_id=task_id,
            task_revision=task_revision,
            start_allowed=False,
            state="committed",
            execution_decision_id=decision_id,
        )

    return _unknown_status(task_id=task_id, task_revision=task_revision)


def evaluate_current_execution_start_status(
    cursor: Any,
    *,
    task_id: str,
    owner_id: str,
    workspace_id: str,
    lock_task: bool,
    initiating_selection_request_id: str | None = None,
) -> ResearchExecutionStartStatusOut:
    """Evaluate the one canonical current-Task Start authority.

    `lock_task=True` is the write-command form. At the final selection write
    boundary, `initiating_selection_request_id` identifies only the request
    currently being committed so its own queued/leased job is not mistaken for
    a competing Start. A blocked row remains visible even when it has the same
    request id; every other current-revision execution-selection fact remains
    visible to the same policy.
    """
    if initiating_selection_request_id is not None and not initiating_selection_request_id.strip():
        raise ValueError("initiating_selection_request_id_invalid")
    lock_clause = " FOR UPDATE OF task" if lock_task else ""
    initiating_job_clause = (
        " AND NOT (job.job_id = %s AND job.state IN ('queued', 'leased'))"
        if initiating_selection_request_id is not None
        else ""
    )
    cursor.execute(
        """SELECT task.payload, task.revision,
                  current_job.state AS current_job_state,
                  current_job.expected_revision AS current_job_expected_revision,
                  current_job.result_payload AS current_job_result_payload,
                  current_job.last_error_code AS current_job_last_error_code
             FROM openair_tasks AS task
             LEFT JOIN LATERAL (
                 SELECT job.state, job.expected_revision,
                        job.result_payload, job.last_error_code,
                        job.created_at, job.updated_at, job.job_id
                   FROM openair_model_jobs AS job
                  WHERE job.resource_kind = 'task'
                    AND job.resource_id = task.task_id
                    AND job.owner_id = task.owner_id
                    AND job.workspace_id = task.workspace_id
                    AND job.job_kind = 'execution_selection'
                    """ + initiating_job_clause + """
                    AND (
                        job.expected_revision = task.revision
                        OR CASE
                             WHEN jsonb_typeof(job.result_payload->'facts'->'taskRevision') = 'number'
                             THEN (job.result_payload->'facts'->>'taskRevision')::numeric = task.revision
                             ELSE FALSE
                           END
                        OR CASE
                             WHEN jsonb_typeof(job.result_payload->'taskRevision') = 'number'
                             THEN (job.result_payload->>'taskRevision')::numeric = task.revision
                             ELSE FALSE
                           END
                    )
                  ORDER BY
                      CASE WHEN job.state = 'blocked' THEN 0 ELSE 1 END,
                      job.updated_at DESC, job.created_at DESC, job.job_id DESC
                  LIMIT 1
             ) AS current_job ON TRUE
            WHERE task.task_id = %s
              AND task.owner_id = %s
              AND task.workspace_id = %s
              AND task.status <> 'deleted'""" + lock_clause,
        (
            *((initiating_selection_request_id,) if initiating_selection_request_id is not None else ()),
            task_id,
            owner_id,
            workspace_id,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        raise ResearchExecutionStartTaskNotFound("research_task_not_found")
    return _current_status_from_row(task_id=task_id, row=row)


class ResearchExecutionStartStatusReader:
    """Read current Start authority or one historical/request-specific receipt."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory | None = None,
        runtime_settings: SettingsLike = settings,
    ) -> None:
        self._settings = runtime_settings
        self._connection_factory = connection_factory or self._connect

    def read_current(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> ResearchExecutionStartStatusOut:
        if self._settings.persistence_backend != "postgres":
            raise RuntimeError("research_execution_requires_postgres")
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                return evaluate_current_execution_start_status(
                    cursor,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    lock_task=False,
                )

    def read(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        selection_request_id: str,
    ) -> ResearchExecutionStartRequestStatusOut:
        """Read one request receipt without projecting it into current Task authority."""
        if self._settings.persistence_backend != "postgres":
            raise RuntimeError("research_execution_requires_postgres")

        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT selection.execution_decision_id,
                              blocked.outcome AS blocked_outcome,
                              queued.state AS queue_state,
                              queued.expected_revision AS queue_expected_revision,
                              queued.result_payload AS queue_result_payload,
                              queued.last_error_code AS queue_last_error_code
                         FROM openair_tasks AS task
                         LEFT JOIN openair_execution_selection_request AS selection
                           ON selection.task_id = task.task_id
                          AND selection.owner_id = task.owner_id
                          AND selection.workspace_id = task.workspace_id
                          AND selection.selection_request_id = %s
                         LEFT JOIN LATERAL (
                             SELECT attempt.outcome
                               FROM openair_execution_start_attempts AS attempt
                              WHERE attempt.task_id = task.task_id
                                AND attempt.owner_id = task.owner_id
                                AND attempt.workspace_id = task.workspace_id
                                AND attempt.operation = 'research_execution_selection'
                                AND attempt.idempotency_key_sha256 = %s
                                AND attempt.outcome LIKE 'blocked:%%'
                              ORDER BY attempt.occurred_at DESC, attempt.attempt_id DESC
                              LIMIT 1
                         ) AS blocked ON TRUE
                         LEFT JOIN openair_model_jobs AS queued
                           ON queued.resource_kind = 'task'
                          AND queued.resource_id = task.task_id
                          AND queued.owner_id = task.owner_id
                          AND queued.workspace_id = task.workspace_id
                          AND queued.job_id = %s
                          AND queued.job_kind = 'execution_selection'
                        WHERE task.task_id = %s
                          AND task.owner_id = %s
                          AND task.workspace_id = %s""",
                    (
                        selection_request_id,
                        hashlib.sha256(selection_request_id.encode("utf-8")).hexdigest(),
                        selection_request_id,
                        task_id,
                        owner_id,
                        workspace_id,
                    ),
                )
                row = cursor.fetchone()

        if row is None:
            raise ResearchExecutionStartTaskNotFound("research_task_not_found")

        request_revision = _revision(row.get("queue_expected_revision"))
        execution_decision_id = str(row.get("execution_decision_id") or "").strip()
        if execution_decision_id:
            return ResearchExecutionStartRequestStatusOut(
                taskId=task_id,
                selectionRequestId=selection_request_id,
                state="committed",
                taskRevision=request_revision,
                executionDecisionId=execution_decision_id,
            )

        blocked = _parse_attempt_blocker(row.get("blocked_outcome"))
        if blocked is not None:
            reason, blocker_revision = blocked
            if blocker_revision is not None:
                return ResearchExecutionStartRequestStatusOut(
                    taskId=task_id,
                    selectionRequestId=selection_request_id,
                    state="blocked",
                    taskRevision=blocker_revision,
                    blockingReasonCode=reason,
                )

        queue_state = str(row.get("queue_state") or "").strip()
        if queue_state == "blocked":
            blocker = _queue_blocker(
                row.get("queue_result_payload"),
                expected_revision=request_revision,
            )
            if blocker is not None:
                reason, blocker_revision = blocker
                if blocker_revision is not None:
                    return ResearchExecutionStartRequestStatusOut(
                        taskId=task_id,
                        selectionRequestId=selection_request_id,
                        state="blocked",
                        taskRevision=blocker_revision,
                        blockingReasonCode=reason,
                    )
            return ResearchExecutionStartRequestStatusOut(
                taskId=task_id,
                selectionRequestId=selection_request_id,
                state="unknown",
                taskRevision=request_revision,
            )
        if queue_state == "queued":
            return ResearchExecutionStartRequestStatusOut(
                taskId=task_id,
                selectionRequestId=selection_request_id,
                state="accepted",
                taskRevision=request_revision,
            )
        if queue_state == "leased":
            return ResearchExecutionStartRequestStatusOut(
                taskId=task_id,
                selectionRequestId=selection_request_id,
                state="running",
                taskRevision=request_revision,
            )
        if queue_state == "failed":
            return ResearchExecutionStartRequestStatusOut(
                taskId=task_id,
                selectionRequestId=selection_request_id,
                state="failed",
                taskRevision=request_revision,
                failureReasonCode=(
                    str(row.get("queue_last_error_code") or "").strip()
                    or "execution_selection_failed"
                ),
            )
        if queue_state == "succeeded":
            payload = _coerce_json_object(row.get("queue_result_payload"))
            decision_id = (
                str(payload.get("executionDecisionId") or "").strip()
                if payload is not None
                else ""
            )
            if decision_id:
                return ResearchExecutionStartRequestStatusOut(
                    taskId=task_id,
                    selectionRequestId=selection_request_id,
                    state="committed",
                    taskRevision=request_revision,
                    executionDecisionId=decision_id,
                )
            return ResearchExecutionStartRequestStatusOut(
                taskId=task_id,
                selectionRequestId=selection_request_id,
                state="unknown",
                taskRevision=request_revision,
            )

        return ResearchExecutionStartRequestStatusOut(
            taskId=task_id,
            selectionRequestId=selection_request_id,
            state="not_committed",
        )

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        import psycopg
        from psycopg.rows import dict_row

        conninfo = (
            f"host={self._settings.postgres_host} port={self._settings.postgres_port} "
            f"dbname={self._settings.postgres_database} user={self._settings.postgres_user} "
            "connect_timeout=2"
        )
        connection = psycopg.connect(
            conninfo,
            password=self._settings.postgres_password,
            row_factory=dict_row,
        )
        try:
            yield connection
        finally:
            connection.close()

"""Durable authority for user-triggered work that may call a model."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

import psycopg
from psycopg.rows import dict_row

from app.config import settings


class ModelJobQueueError(RuntimeError):
    pass


class ModelJobConflict(ModelJobQueueError):
    pass


class ModelJobStartDenied(ModelJobConflict):
    """A new initial Start lost the canonical current-Task authority check."""

    def __init__(self, start_status: Any) -> None:
        super().__init__("execution_start_not_allowed")
        self.start_status = start_status


class ModelJobContinueDenied(ModelJobConflict):
    pass


class ModelJobResourceNotFound(ModelJobQueueError):
    pass


@dataclass(frozen=True)
class ModelJobLease:
    resource_kind: str
    resource_id: str
    owner_id: str
    workspace_id: str
    job_id: str
    job_kind: str
    expected_revision: int | None
    command_payload: dict[str, Any]
    lease_owner: str
    lease_epoch: int
    attempt_count: int


def model_job_command_hash(
    *,
    resource_kind: str,
    resource_id: str,
    owner_id: str,
    workspace_id: str,
    job_id: str,
    job_kind: str,
    expected_revision: int | None,
    command_payload: Mapping[str, Any],
) -> str:
    payload = json.dumps(
        {
            "commandPayload": dict(command_payload),
            "expectedRevision": expected_revision,
            "jobId": job_id,
            "jobKind": job_kind,
            "ownerId": owner_id,
            "resourceId": resource_id,
            "resourceKind": resource_kind,
            "workspaceId": workspace_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ModelJobQueueRepository:
    _KINDS = {
        "execution_selection": "task",
        "research_design_agent_revision": "task",
        "research_design_plan_revision": "task",
        "agent_session_turn": "agent_session",
    }

    def is_exact_replay(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        owner_id: str,
        workspace_id: str,
        job_id: str,
        job_kind: str,
        expected_revision: int | None,
        command_payload: Mapping[str, Any],
    ) -> bool:
        command_hash = model_job_command_hash(
            resource_kind=resource_kind,
            resource_id=resource_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            job_id=job_id,
            job_kind=job_kind,
            expected_revision=expected_revision,
            command_payload=command_payload,
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._scope_resource(
                    cursor,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    lock=False,
                )
                cursor.execute(
                    """SELECT command_hash FROM openair_model_jobs
                         WHERE resource_kind=%s AND resource_id=%s
                           AND owner_id=%s AND workspace_id=%s AND job_id=%s""",
                    (resource_kind, resource_id, owner_id, workspace_id, job_id),
                )
                existing = cursor.fetchone()
                return bool(
                    existing is not None
                    and existing["command_hash"] == command_hash
                )

    def enqueue(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        owner_id: str,
        workspace_id: str,
        job_id: str,
        job_kind: str,
        expected_revision: int | None,
        command_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._KINDS.get(job_kind) != resource_kind:
            raise ValueError("model_job_kind_invalid")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise ValueError("expected_revision_invalid")
        if resource_kind == "task" and expected_revision is None:
            raise ValueError("expected_revision_required")
        command_hash = model_job_command_hash(
            resource_kind=resource_kind,
            resource_id=resource_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            job_id=job_id,
            job_kind=job_kind,
            expected_revision=expected_revision,
            command_payload=command_payload,
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                current_revision = self._lock_resource(
                    cursor,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT * FROM openair_model_jobs
                         WHERE resource_kind=%s AND resource_id=%s
                           AND owner_id=%s AND workspace_id=%s AND job_id=%s
                         FOR UPDATE""",
                    (resource_kind, resource_id, owner_id, workspace_id, job_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if existing["command_hash"] != command_hash:
                        raise ModelJobConflict("model_job_command_conflict")
                    return dict(existing)
                if expected_revision is not None and current_revision != expected_revision:
                    raise ModelJobConflict("task_revision_conflict")

                request_kind = str(command_payload.get("requestKind") or "").strip()
                if (
                    resource_kind == "task"
                    and job_kind == "execution_selection"
                    and request_kind == "continue_research"
                ):
                    from app.modules.workbench_user_actions import (
                        project_continue_research_action,
                    )

                    action = project_continue_research_action(
                        self._locked_task_payload(
                            cursor,
                            task_id=resource_id,
                            owner_id=owner_id,
                            workspace_id=workspace_id,
                        )
                    )
                    if action["executionStopped"] or not action["allowed"]:
                        raise ModelJobContinueDenied("continue_research_not_allowed")
                if (
                    resource_kind == "task"
                    and job_kind == "execution_selection"
                    and request_kind in {"explicit_start", "confirmed_design"}
                ):
                    # Fail-fast admission only. The final non-bypassable
                    # authority is re-evaluated again inside
                    # ResearchOrchestrationRepository.commit_prepared_selection().
                    from app.modules.research_execution_start_status import (
                        ResearchExecutionStartTaskNotFound,
                        evaluate_current_execution_start_status,
                    )

                    try:
                        start_status = evaluate_current_execution_start_status(
                            cursor,
                            task_id=resource_id,
                            owner_id=owner_id,
                            workspace_id=workspace_id,
                            lock_task=True,
                        )
                    except ResearchExecutionStartTaskNotFound as exc:
                        raise ModelJobResourceNotFound("research_task_not_found") from exc
                    if not start_status.startAllowed:
                        raise ModelJobStartDenied(start_status)

                cursor.execute(
                    """INSERT INTO openair_model_jobs (
                           resource_kind, resource_id, owner_id, workspace_id,
                           job_id, job_kind, expected_revision, command_hash,
                           command_payload, runtime_source_version
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                       RETURNING *""",
                    (
                        resource_kind,
                        resource_id,
                        owner_id,
                        workspace_id,
                        job_id,
                        job_kind,
                        expected_revision,
                        command_hash,
                        json.dumps(dict(command_payload), ensure_ascii=False),
                        settings.runtime_source_version,
                    ),
                )
                inserted = dict(cursor.fetchone())
                if job_kind == "agent_session_turn":
                    cursor.execute(
                        """UPDATE openair_agent_sessions
                              SET status='running', updated_at=clock_timestamp()
                            WHERE session_id=%s AND owner_id=%s AND workspace_id=%s""",
                        (resource_id, owner_id, workspace_id),
                    )
                return inserted

    def reconcile_confirmed_design_jobs(self, *, limit: int = 16) -> int:
        """Repair the small confirmation-to-enqueue crash window.

        Confirmation is authoritative user state.  If the API process exits
        immediately after committing it, the worker reconstructs the exact
        deterministic auto-start intent from the confirmed aggregate.
        """
        from app.models.research_orchestration import (
            ResearchDesignStatus,
            ResearchTaskV12,
        )
        from app.modules.research_execution_start_status import (
            ResearchExecutionStartTaskNotFound,
            evaluate_current_execution_start_status,
        )

        page_size = max(1, min(int(limit), 100))
        enqueued = 0
        after: tuple[Any, str, str, str] | None = None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                while enqueued < page_size:
                    after_params = (
                        (None, None, None, None, None)
                        if after is None
                        else (after[0], *after)
                    )
                    cursor.execute(
                        """SELECT task.task_id, task.owner_id, task.workspace_id,
                                  task.revision, task.payload, task.updated_at
                             FROM openair_tasks AS task
                             JOIN openair_task_execution_policies AS policy
                               ON policy.task_id=task.task_id
                              AND policy.owner_id=task.owner_id
                              AND policy.workspace_id=task.workspace_id
                            WHERE task.status NOT IN ('deleted', 'completed', 'failed', 'cancelled')
                              AND policy.current_mode IN ('automatic_research', 'stage_confirmation')
                              AND task.payload ? 'activeResearchDesignRef'
                              AND task.payload ? 'researchDesignVersions'
                              AND NOT EXISTS (
                                  SELECT 1 FROM openair_model_jobs AS job
                                   WHERE job.resource_kind='task'
                                     AND job.resource_id=task.task_id
                                     AND job.owner_id=task.owner_id
                                     AND job.workspace_id=task.workspace_id
                                     AND job.job_kind='execution_selection'
                                     AND job.command_payload->>'requestKind'='confirmed_design'
                                     AND job.job_id = (
                                         'research-design:auto-start:'
                                         || (task.payload->'activeResearchDesignRef'->>'researchDesignId')
                                         || ':v'
                                         || (task.payload->'activeResearchDesignRef'->>'version')
                                     )
                              )
                              AND (
                                  %s::timestamptz IS NULL
                                  OR (task.updated_at, task.task_id, task.owner_id, task.workspace_id)
                                     > (%s::timestamptz, %s, %s, %s)
                              )
                            ORDER BY task.updated_at, task.task_id,
                                     task.owner_id, task.workspace_id
                            LIMIT %s""",
                        (*after_params, page_size),
                    )
                    candidates = list(cursor.fetchall())
                    if not candidates:
                        break
                    tail = candidates[-1]
                    after = (
                        tail["updated_at"],
                        str(tail["task_id"]),
                        str(tail["owner_id"]),
                        str(tail["workspace_id"]),
                    )

                    for row in candidates:
                        try:
                            raw = row["payload"]
                            if isinstance(raw, str):
                                raw = json.loads(raw)
                            aliases = {
                                field.alias or name
                                for name, field in ResearchTaskV12.model_fields.items()
                            }
                            task = ResearchTaskV12.model_validate(
                                {alias: raw[alias] for alias in aliases}
                            )
                            active = task.active_research_design_ref
                            latest = max(
                                task.research_design_versions,
                                key=lambda item: item.version,
                                default=None,
                            )
                            if (
                                active is None
                                or latest is None
                                or latest.status is not ResearchDesignStatus.CONFIRMED
                                or active.research_design_id != latest.research_design_id
                                or active.version != latest.version
                            ):
                                continue

                            start_status = evaluate_current_execution_start_status(
                                cursor,
                                task_id=str(row["task_id"]),
                                owner_id=str(row["owner_id"]),
                                workspace_id=str(row["workspace_id"]),
                                lock_task=False,
                            )
                            if not start_status.startAllowed:
                                continue

                            job_id = (
                                "research-design:auto-start:"
                                f"{active.research_design_id}:v{active.version}"
                            )
                            result = self.enqueue(
                                resource_kind="task",
                                resource_id=str(row["task_id"]),
                                owner_id=str(row["owner_id"]),
                                workspace_id=str(row["workspace_id"]),
                                job_id=job_id,
                                job_kind="execution_selection",
                                expected_revision=int(row["revision"]),
                                command_payload={"requestKind": "confirmed_design"},
                            )
                            if str(result.get("state")) == "queued":
                                enqueued += 1
                                if enqueued >= page_size:
                                    break
                        except (
                            KeyError,
                            ModelJobConflict,
                            ModelJobResourceNotFound,
                            ResearchExecutionStartTaskNotFound,
                            ValueError,
                            TypeError,
                        ):
                            continue

                    if len(candidates) < page_size:
                        break
        return enqueued

    def read(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        owner_id: str,
        workspace_id: str,
        job_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._scope_resource(
                    cursor,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                cursor.execute(
                    """SELECT job_id, job_kind, state, result_payload,
                              last_error_code, created_at, updated_at
                         FROM openair_model_jobs
                        WHERE resource_kind=%s AND resource_id=%s
                          AND owner_id=%s AND workspace_id=%s AND job_id=%s""",
                    (resource_kind, resource_id, owner_id, workspace_id, job_id),
                )
                row = cursor.fetchone()
                return dict(row) if row is not None else None

    def claim(self, *, lease_owner: str, lease_seconds: int) -> ModelJobLease | None:
        if not lease_owner.strip() or lease_seconds <= 0:
            raise ValueError("model_job_lease_invalid")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT * FROM openair_model_jobs
                        WHERE state='leased' AND lease_expires_at <= clock_timestamp()
                        ORDER BY lease_expires_at, created_at, job_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 64"""
                )
                for expired in cursor.fetchall():
                    reconciled = self._reconciled_business_result(cursor, expired)
                    terminal_state = (
                        "succeeded" if reconciled is not None else "external_result_unknown"
                    )
                    cursor.execute(
                        """UPDATE openair_model_jobs
                              SET state=%s, result_payload=%s::jsonb,
                                  lease_owner=NULL, lease_expires_at=NULL,
                                  last_error_code=%s, updated_at=clock_timestamp()
                            WHERE resource_kind=%s AND resource_id=%s
                              AND owner_id=%s AND workspace_id=%s AND job_id=%s
                              AND state='leased' AND lease_epoch=%s
                              AND lease_expires_at <= clock_timestamp()""",
                        (
                            terminal_state,
                            (
                                json.dumps(reconciled, ensure_ascii=False)
                                if reconciled is not None
                                else None
                            ),
                            None if reconciled is not None else "worker_lease_expired",
                            expired["resource_kind"],
                            expired["resource_id"],
                            expired["owner_id"],
                            expired["workspace_id"],
                            expired["job_id"],
                            expired["lease_epoch"],
                        ),
                    )
                cursor.execute(
                    """SELECT * FROM openair_model_jobs
                        WHERE state='queued' AND next_attempt_at <= clock_timestamp()
                          AND runtime_source_version=%s
                        ORDER BY created_at, job_id
                        FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (settings.runtime_source_version,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                cursor.execute(
                    """UPDATE openair_model_jobs
                          SET state='leased', lease_owner=%s, lease_epoch=lease_epoch+1,
                              lease_expires_at=clock_timestamp() + make_interval(secs => %s),
                              attempt_count=attempt_count+1, updated_at=clock_timestamp()
                        WHERE resource_kind=%s AND resource_id=%s
                          AND owner_id=%s AND workspace_id=%s AND job_id=%s
                          AND state='queued'
                        RETURNING *""",
                    (
                        lease_owner,
                        lease_seconds,
                        row["resource_kind"],
                        row["resource_id"],
                        row["owner_id"],
                        row["workspace_id"],
                        row["job_id"],
                    ),
                )
                claimed = cursor.fetchone()
                if claimed is None:
                    return None
                raw_payload = claimed.get("command_payload")
                if isinstance(raw_payload, str):
                    raw_payload = json.loads(raw_payload)
                return ModelJobLease(
                    resource_kind=str(claimed["resource_kind"]),
                    resource_id=str(claimed["resource_id"]),
                    owner_id=str(claimed["owner_id"]),
                    workspace_id=str(claimed["workspace_id"]),
                    job_id=str(claimed["job_id"]),
                    job_kind=str(claimed["job_kind"]),
                    expected_revision=(
                        int(claimed["expected_revision"])
                        if claimed["expected_revision"] is not None
                        else None
                    ),
                    command_payload=dict(raw_payload or {}),
                    lease_owner=str(claimed["lease_owner"]),
                    lease_epoch=int(claimed["lease_epoch"]),
                    attempt_count=int(claimed["attempt_count"]),
                )

    def renew(self, lease: ModelJobLease, *, lease_seconds: int) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE openair_model_jobs
                          SET lease_expires_at=clock_timestamp() + make_interval(secs => %s),
                              updated_at=clock_timestamp()
                        WHERE resource_kind=%s AND resource_id=%s
                          AND owner_id=%s AND workspace_id=%s AND job_id=%s
                          AND state='leased' AND lease_owner=%s AND lease_epoch=%s
                          AND lease_expires_at > clock_timestamp()""",
                    (
                        lease_seconds,
                        lease.resource_kind,
                        lease.resource_id,
                        lease.owner_id,
                        lease.workspace_id,
                        lease.job_id,
                        lease.lease_owner,
                        lease.lease_epoch,
                    ),
                )
                return cursor.rowcount == 1

    def finish(
        self,
        lease: ModelJobLease,
        *,
        state: str,
        result_payload: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> bool:
        if state not in {"succeeded", "blocked", "failed"}:
            raise ValueError("model_job_terminal_state_invalid")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                if retryable:
                    cursor.execute(
                        """SELECT * FROM openair_model_jobs
                            WHERE resource_kind=%s AND resource_id=%s
                              AND owner_id=%s AND workspace_id=%s AND job_id=%s
                              AND state='leased' AND lease_owner=%s AND lease_epoch=%s
                              AND lease_expires_at > clock_timestamp()
                            FOR UPDATE""",
                        (
                            lease.resource_kind,
                            lease.resource_id,
                            lease.owner_id,
                            lease.workspace_id,
                            lease.job_id,
                            lease.lease_owner,
                            lease.lease_epoch,
                        ),
                    )
                    active = cursor.fetchone()
                    if active is None:
                        return False
                    reconciled = self._reconciled_business_result(cursor, active)
                    terminal_state = (
                        "succeeded" if reconciled is not None else "external_result_unknown"
                    )
                    cursor.execute(
                        """UPDATE openair_model_jobs
                              SET state=%s, result_payload=%s::jsonb,
                                  lease_owner=NULL, lease_expires_at=NULL,
                                  last_error_code=%s, updated_at=clock_timestamp()
                            WHERE resource_kind=%s AND resource_id=%s
                              AND owner_id=%s AND workspace_id=%s AND job_id=%s
                              AND state='leased' AND lease_owner=%s AND lease_epoch=%s
                              AND lease_expires_at > clock_timestamp()
                            RETURNING state""",
                        (
                            terminal_state,
                            (
                                json.dumps(reconciled, ensure_ascii=False)
                                if reconciled is not None
                                else None
                            ),
                            None if reconciled is not None else error_code,
                            lease.resource_kind,
                            lease.resource_id,
                            lease.owner_id,
                            lease.workspace_id,
                            lease.job_id,
                            lease.lease_owner,
                            lease.lease_epoch,
                        ),
                    )
                else:
                    cursor.execute(
                        """UPDATE openair_model_jobs
                              SET state=%s, result_payload=%s::jsonb,
                                  lease_owner=NULL, lease_expires_at=NULL,
                                  last_error_code=%s, updated_at=clock_timestamp()
                            WHERE resource_kind=%s AND resource_id=%s
                              AND owner_id=%s AND workspace_id=%s AND job_id=%s
                              AND state='leased' AND lease_owner=%s AND lease_epoch=%s
                              AND lease_expires_at > clock_timestamp()
                            RETURNING state""",
                        (
                            state,
                            (
                                json.dumps(dict(result_payload), ensure_ascii=False)
                                if result_payload is not None
                                else None
                            ),
                            error_code,
                            lease.resource_kind,
                            lease.resource_id,
                            lease.owner_id,
                            lease.workspace_id,
                            lease.job_id,
                            lease.lease_owner,
                            lease.lease_epoch,
                        ),
                    )
                finished = cursor.fetchone()
                if (
                    finished is not None
                    and str(finished["state"]) == "failed"
                    and lease.resource_kind == "agent_session"
                ):
                    cursor.execute(
                        """UPDATE openair_agent_sessions
                              SET status='failed', updated_at=clock_timestamp()
                            WHERE session_id=%s AND owner_id=%s AND workspace_id=%s""",
                        (lease.resource_id, lease.owner_id, lease.workspace_id),
                    )
                return finished is not None

    @staticmethod
    def _reconciled_business_result(
        cursor: Any, job: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Return only outcomes already proven by durable product state.

        This method never calls a Provider and never re-executes a handler. It
        recognizes idempotency identities the existing handlers already persist
        on successful completion. Absence of proof stays unknown.
        """
        job_kind = str(job.get("job_kind") or "")
        resource_id = str(job.get("resource_id") or "")
        owner_id = str(job.get("owner_id") or "")
        workspace_id = str(job.get("workspace_id") or "")
        job_id = str(job.get("job_id") or "")

        if job_kind == "agent_session_turn":
            cursor.execute(
                """SELECT status, payload, updated_at
                     FROM openair_agent_sessions
                    WHERE session_id=%s AND owner_id=%s AND workspace_id=%s""",
                (resource_id, owner_id, workspace_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            payload = row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            requests = (
                (payload or {}).get("turnRequests")
                if isinstance(payload, Mapping)
                else None
            )
            if not isinstance(requests, Mapping) or requests.get(job_id) != "completed":
                return None
            updated_at = row.get("updated_at")
            return {
                "sessionStatus": str(row.get("status") or ""),
                "updatedAt": (
                    updated_at.isoformat()
                    if callable(getattr(updated_at, "isoformat", None))
                    else str(updated_at or "")
                ),
                "reconciledFromDurableState": True,
            }

        if job_kind not in {
            "execution_selection",
            "research_design_agent_revision",
            "research_design_plan_revision",
        }:
            return None

        cursor.execute(
            """SELECT revision, payload
                 FROM openair_tasks
                WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                  AND status <> 'deleted'""",
            (resource_id, owner_id, workspace_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        payload = row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, Mapping):
            return None

        if job_kind == "execution_selection":
            decisions = payload.get("executionDecisions")
            if not isinstance(decisions, list):
                return None
            decision = next(
                (
                    item
                    for item in decisions
                    if isinstance(item, Mapping)
                    and item.get("selectionRequestId") == job_id
                    and item.get("executionDecisionId")
                ),
                None,
            )
            if decision is None:
                return None
            return {
                "executionDecisionId": str(decision["executionDecisionId"]),
                "taskRevision": int(row["revision"]),
                "reconciledFromDurableState": True,
            }

        attempts = payload.get("planningAttempts")
        if not isinstance(attempts, list):
            return None
        completed = any(
            isinstance(item, Mapping)
            and item.get("requestId") == job_id
            and item.get("status") == "completed"
            for item in attempts
        )
        if not completed:
            return None
        return {
            "taskRevision": int(row["revision"]),
            "reconciledFromDurableState": True,
        }

    @staticmethod
    def _scope_resource(
        cursor: Any,
        *,
        resource_kind: str,
        resource_id: str,
        owner_id: str,
        workspace_id: str,
        lock: bool = False,
    ) -> int | None:
        if resource_kind == "task":
            cursor.execute(
                """SELECT revision FROM openair_tasks
                    WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                      AND status <> 'deleted'""" + (" FOR UPDATE" if lock else ""),
                (resource_id, owner_id, workspace_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ModelJobResourceNotFound("research_task_not_found")
            return int(row["revision"])
        if resource_kind == "agent_session":
            cursor.execute(
                """SELECT 1 FROM openair_agent_sessions
                    WHERE session_id=%s AND owner_id=%s AND workspace_id=%s"""
                + (" FOR UPDATE" if lock else ""),
                (resource_id, owner_id, workspace_id),
            )
            if cursor.fetchone() is None:
                raise ModelJobResourceNotFound("agent_session_not_found")
            return None
        raise ValueError("model_job_resource_kind_invalid")

    def _lock_resource(self, cursor: Any, **kwargs: Any) -> int | None:
        return self._scope_resource(cursor, lock=True, **kwargs)

    @staticmethod
    def _locked_task_payload(
        cursor: Any,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        cursor.execute(
            """SELECT payload, revision, status FROM openair_tasks
                WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                  AND status <> 'deleted'""",
            (task_id, owner_id, workspace_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ModelJobResourceNotFound("research_task_not_found")
        raw_payload = row.get("payload")
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
        payload.update({
            "taskId": task_id,
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "revision": int(row["revision"]),
            "status": str(row["status"]),
        })
        return payload

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        connection = psycopg.connect(
            (
                f"host={settings.postgres_host} port={settings.postgres_port} "
                f"dbname={settings.postgres_database} user={settings.postgres_user} "
                "connect_timeout=2"
            ),
            password=settings.postgres_password,
            row_factory=dict_row,
        )
        try:
            with connection.transaction():
                yield connection
        finally:
            connection.close()


__all__ = [
    "ModelJobConflict",
    "ModelJobLease",
    "ModelJobQueueRepository",
    "ModelJobResourceNotFound",
    "ModelJobStartDenied",
    "model_job_command_hash",
]

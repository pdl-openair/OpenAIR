"""Atomic durable-finalization transactions for Research Start."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.modules.research_start_attempt_lease import ATTEMPT_COLUMNS
from app.modules.research_start_errors import (
    FinalizeIdentityConflictError,
    FinalizeInvariantBrokenError,
    LeaseLostError,
    TerminalStateError,
)
from app.modules.research_start_projection import _json, _row


class ResearchStartFinalizationOperations:
    """Execute finalization through a compatible repository façade."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def complete_existing_task_request(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        task_id: str,
        result: Mapping[str, Any],
        user_message: str,
    ) -> dict[str, Any]:
        """Compatibility wrapper that cannot bypass receipt validation."""
        self._repository._require_nonempty(
            request_id=request_id,
            worker_id=worker_id,
            task_id=task_id,
            user_message=user_message,
        )
        if not isinstance(result, Mapping) or result.get("taskId") != task_id:
            raise ValueError("result must identify the existing Task")
        finalized = self._repository.finalize_from_authoritative_receipt(
            request_id,
            worker_id,
            lease_epoch,
            user_message=user_message,
        )
        if finalized.get("finalized") is not True:
            raise FinalizeInvariantBrokenError()
        receipt = finalized.get("receipt")
        if not isinstance(receipt, Mapping) or receipt.get("taskId") != task_id:
            raise FinalizeIdentityConflictError()
        session = finalized.get("session")
        if not isinstance(session, Mapping):
            raise FinalizeInvariantBrokenError()
        return dict(session)

    def finalize_authoritative_receipt_transaction(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        user_message: str,
        require_succeeded_attempt: bool,
    ) -> dict[str, Any]:
        """Validate the canonical receipt and complete its request in one transaction."""
        self._repository._require_nonempty(
            request_id=request_id,
            worker_id=worker_id,
            user_message=user_message,
        )
        with self._repository._connect() as conn:
            with conn.cursor() as cur:
                # Keep session -> request -> attempt -> Task lock order. The Task
                # row stays locked during receipt validation and session closure.
                session, request = self._repository._lock_path(cur, request_id)
                self._repository._require_live_lease(session, request, worker_id, lease_epoch)
                cur.execute(
                    f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
                          WHERE request_id = %s AND phase = 'research_planner'
                            AND state IN ('started', 'succeeded')
                          ORDER BY ordinal DESC, started_at DESC, attempt_id DESC
                          LIMIT 1 FOR UPDATE""",
                    (request_id,),
                )
                attempt = _row(cur.fetchone())
                failure_reason = None
                if attempt is None:
                    failure_reason = "planner_attempt_missing"
                elif require_succeeded_attempt and attempt.get("state") != "succeeded":
                    failure_reason = "planner_attempt_not_succeeded"

                receipt = None
                if failure_reason is None and attempt is not None:
                    receipt = self._repository._committed_design_receipt(
                        cur, session, request, attempt
                    )
                    if receipt is None:
                        failure_reason = "authoritative_receipt_unavailable"

                if failure_reason is not None:
                    if not require_succeeded_attempt:
                        return {"finalized": False, "reason": failure_reason}
                    failed = self._repository._close_authoritative_finalize_failure(
                        cur,
                        session,
                        request,
                        worker_id=worker_id,
                        lease_epoch=lease_epoch,
                        reason=failure_reason,
                    )
                    return {
                        "finalized": False,
                        "closed": True,
                        "reason": failure_reason,
                        "session": failed,
                    }
                if attempt is None or receipt is None:
                    raise FinalizeInvariantBrokenError()
                completed = self._repository._complete_committed_design_lineage(
                    cur,
                    session,
                    request,
                    attempt,
                    receipt,
                    worker_id=worker_id,
                    lease_epoch=lease_epoch,
                    require_expired_lease=False,
                    user_message=(
                        "Research design is already confirmed."
                        if receipt.get("researchDesignStatus") == "confirmed"
                        else user_message
                    ),
                )
                if completed is None:
                    raise FinalizeInvariantBrokenError()
                return {
                    "finalized": True,
                    "reason": None,
                    "receipt": dict(receipt),
                    "session": completed,
                }

    def finalize_from_authoritative_receipt(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        user_message: str,
    ) -> dict[str, Any]:
        """Finalize a normally finished Planner attempt from durable facts."""
        return self.finalize_authoritative_receipt_transaction(
            request_id,
            worker_id,
            lease_epoch,
            user_message=user_message,
            require_succeeded_attempt=True,
        )

    def reconcile_committed_design_request(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        user_message: str,
    ) -> dict[str, Any] | None:
        """Close a committed-design crash window without another Planner call."""
        finalized = self.finalize_authoritative_receipt_transaction(
            request_id,
            worker_id,
            lease_epoch,
            user_message=user_message,
            require_succeeded_attempt=False,
        )
        if finalized.get("finalized") is not True:
            return None
        session = finalized.get("session")
        return dict(session) if isinstance(session, Mapping) else None

    def finalize_task_request(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        task: Mapping[str, Any],
        artifact: Mapping[str, Any],
        result: Mapping[str, Any],
        user_message: str,
    ) -> dict[str, str]:
        """Atomically write the reserved Task, Artifact, and terminal outcome."""
        self._repository._require_nonempty(
            request_id=request_id,
            worker_id=worker_id,
            user_message=user_message,
        )
        if (
            not isinstance(task, Mapping)
            or not isinstance(artifact, Mapping)
            or not isinstance(result, Mapping)
        ):
            raise ValueError("task, artifact, and result must be JSON objects")
        with self._repository._connect() as conn:
            with conn.cursor() as cur:
                session, request = self._repository._lock_path(cur, request_id)
                request_payload = request.get("request_payload") or {}
                if isinstance(request_payload, str):
                    request_payload = json.loads(request_payload)
                created_by = str(
                    request_payload.get("_createdBy", request.get("owner_id", session["owner_id"]))
                )
                expected = self._repository._finalize_result(session)
                request_succeeded = request["state"] == "succeeded"
                session_completed = session["state"] == "completed"
                if request_succeeded or session_completed:
                    if not (request_succeeded and session_completed):
                        raise FinalizeInvariantBrokenError()
                    if (
                        request.get("result_payload") != expected
                        or session.get("result_payload") != expected
                    ):
                        raise FinalizeInvariantBrokenError()
                    stored_task, stored_artifact = self._repository._load_finalize_replay_rows(
                        cur, session
                    )
                    embedded = stored_task.get("artifacts")
                    if (
                        not isinstance(embedded, list)
                        or len(embedded) != 1
                        or not isinstance(embedded[0], Mapping)
                    ):
                        raise FinalizeInvariantBrokenError()
                    self._repository._assert_finalize_payload(
                        session, stored_task, embedded[0], expected, created_by
                    )
                    if any(
                        not self._repository._artifact_replay_value_matches(
                            key, value, stored_artifact.get(key)
                        )
                        for key, value in embedded[0].items()
                    ):
                        raise FinalizeInvariantBrokenError()
                    return expected
                expected = self._repository._assert_finalize_payload(
                    session, task, artifact, result, created_by
                )
                self._repository._require_live_lease(session, request, worker_id, lease_epoch)
                self._repository._assert_no_started_attempt(cur, request_id)
                self._repository._assert_exact_succeeded_attempts(cur, request_id)
                from app.modules.artifact_repository import ArtifactRepository
                from app.modules.task_repository import TaskRepository

                try:
                    TaskRepository().insert_task_with_cursor(cur, dict(task))
                    ArtifactRepository().insert_artifact_with_cursor(
                        cur,
                        str(session["reserved_task_id"]),
                        dict(artifact),
                        str(task.get("title") or ""),
                    )
                except Exception as exc:
                    if getattr(exc, "sqlstate", None) == "23505":
                        raise FinalizeIdentityConflictError() from exc
                    raise
                payload = _json(expected)
                cur.execute(
                    """UPDATE research_start_requests
                          SET state = 'succeeded', lease_owner = NULL, lease_expires_at = NULL,
                              result_payload = %s::jsonb, updated_at = clock_timestamp()
                        WHERE request_id = %s AND state = 'leased' AND lease_owner = %s """
                    """AND lease_epoch = %s
                          AND lease_expires_at > clock_timestamp()""",
                    (payload, request_id, worker_id, lease_epoch),
                )
                if cur.rowcount != 1:
                    raise LeaseLostError()
                cur.execute(
                    """UPDATE research_start_sessions
                          SET state = 'completed', result_payload = %s::jsonb, user_message = %s,
                              failure_code = NULL, updated_at = clock_timestamp()
                        WHERE session_id = %s AND owner_id = %s AND workspace_id = %s """
                    """AND state = 'running'""",
                    (
                        payload,
                        user_message,
                        session["session_id"],
                        session["owner_id"],
                        session["workspace_id"],
                    ),
                )
                if cur.rowcount != 1:
                    raise TerminalStateError()
                return expected

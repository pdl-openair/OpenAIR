"""Recovery transactions for durable Research Start requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.research_start_attempt_lease import (
    ATTEMPT_COLUMNS,
    REQUEST_COLUMNS,
    SESSION_COLUMNS,
)
from app.modules.research_start_errors import LeaseLostError, TerminalStateError
from app.modules.research_start_projection import _json, _reserve_public, _row
from app.modules.research_start_response_evidence import merge_attempt_result
from app.modules.research_task_lifecycle import ResearchStartLifecycleFact


class ResearchStartRecoveryOperations:
    """Execute recovery through the repository's compatibility façade."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def resolve_known_planner_http_failure(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        attempt_id: str,
        *,
        failure_result: Mapping[str, Any],
        automatic_recovery_limit: int,
        terminal_user_message: str,
    ) -> str | None:
        """Persist a known Planner failure and only queue a bounded continuation."""
        repo = self._repository
        repo._require_nonempty(
            request_id=request_id,
            worker_id=worker_id,
            attempt_id=attempt_id,
            terminal_user_message=terminal_user_message,
        )
        if lease_epoch <= 0:
            raise ValueError("lease_epoch must be positive")
        if automatic_recovery_limit < 0:
            raise ValueError("automatic_recovery_limit must be non-negative")
        if not isinstance(failure_result, Mapping):
            raise ValueError("failure_result must be a JSON object")
        if failure_result.get("failureCode") != "provider_http_rejected":
            return None
        diagnostic = failure_result.get("providerDiagnostic")
        if not isinstance(diagnostic, Mapping) or not isinstance(diagnostic.get("httpStatus"), int):
            return None

        with repo._connect() as conn:
            with conn.cursor() as cur:
                session, request = repo._lock_path(cur, request_id)
                repo._require_live_lease(session, request, worker_id, lease_epoch)
                if session["state"] != "running":
                    raise TerminalStateError()
                attempt = repo._lock_attempt(cur, request_id, attempt_id)
                if (
                    attempt["phase"] != "research_planner"
                    or attempt["fence_epoch"] != lease_epoch
                    or attempt["state"] != "started"
                ):
                    return None
                cur.execute(
                    """SELECT count(*) AS recovery_count
                         FROM research_start_attempts a
                         JOIN research_start_requests r ON r.request_id = a.request_id
                        WHERE r.session_id = %s AND r.owner_id = %s AND r.workspace_id = %s
                          AND a.phase = 'research_planner' AND a.state = 'failed'
                          AND a.result_payload ->> 'recoveryAction' = 'durable_retry_queued'""",
                    (session["session_id"], session["owner_id"], session["workspace_id"]),
                )
                recovery_count = int((_row(cur.fetchone()) or {}).get("recovery_count") or 0)
                should_recover = recovery_count < automatic_recovery_limit
                persisted_failure = dict(failure_result)
                persisted_failure["recoveryAction"] = (
                    "durable_retry_queued" if should_recover else "durable_retry_budget_exhausted"
                )
                repo._finish_started_attempt_locked(
                    cur,
                    session=session,
                    request=request,
                    worker_id=worker_id,
                    lease_epoch=lease_epoch,
                    attempt_id=attempt_id,
                    result=persisted_failure,
                )
                repo._close_leased_request_as_failed_locked(
                    cur,
                    session=session,
                    request=request,
                    worker_id=worker_id,
                    lease_epoch=lease_epoch,
                )
                if should_recover:
                    repo._enforce_durable_admission(
                        cur, session["owner_id"], session["workspace_id"]
                    )
                    retry_request = repo._insert_continuation_request_locked(
                        cur, session=session, prior_request=request
                    )
                    repo._copy_attempts_to_continuation_locked(
                        cur,
                        source_request_id=request_id,
                        target_request_id=retry_request["request_id"],
                        states=("succeeded",),
                    )
                    cur.execute(
                        f"""UPDATE research_start_sessions
                              SET updated_at = clock_timestamp()
                            WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                              AND state = 'running'
                            RETURNING {SESSION_COLUMNS}""",
                        (session["session_id"], session["owner_id"], session["workspace_id"]),
                    )
                    if _row(cur.fetchone()) is None:
                        raise TerminalStateError()
                    return "recovery_queued"
                repo._apply_reserved_planning_task_lifecycle_fact(
                    cur,
                    session,
                    fact=ResearchStartLifecycleFact.PLANNING_FAILED,
                )
                cur.execute(
                    f"""UPDATE research_start_sessions
                          SET state = 'failed', result_payload = NULL, user_message = %s,
                              failure_code = %s, updated_at = clock_timestamp()
                        WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                          AND state = 'running'
                        RETURNING {SESSION_COLUMNS}""",
                    (
                        terminal_user_message,
                        str(failure_result["failureCode"]),
                        session["session_id"],
                        session["owner_id"],
                        session["workspace_id"],
                    ),
                )
                if _row(cur.fetchone()) is None:
                    raise TerminalStateError()
                return "terminal"

    def retry_external_result_unknown(
        self, owner_id: str, workspace_id: str, session_id: str
    ) -> dict[str, Any]:
        """Resume only after durable evidence proves the Provider was not submitted."""
        repo = self._repository
        repo._require_nonempty(owner_id=owner_id, workspace_id=workspace_id, session_id=session_id)
        with repo._connect() as conn:
            with conn.cursor() as cur:
                session = repo._lock_session(cur, session_id, owner_id, workspace_id)
                if session["state"] != "external_result_unknown":
                    raise TerminalStateError()
                cur.execute(
                    f"""SELECT {REQUEST_COLUMNS} FROM research_start_requests
                          WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                            AND state = 'unknown'
                          ORDER BY updated_at DESC, created_at DESC, request_id DESC
                          LIMIT 1 FOR UPDATE""",
                    (session_id, owner_id, workspace_id),
                )
                prior = _row(cur.fetchone())
                if prior is None:
                    raise TerminalStateError()
                cur.execute(
                    f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
                          WHERE request_id = %s AND state = 'unknown'
                          ORDER BY started_at DESC, attempt_id DESC
                          LIMIT 1 FOR UPDATE""",
                    (prior["request_id"],),
                )
                uncertain_attempt = _row(cur.fetchone())
                if uncertain_attempt is None:
                    raise TerminalStateError()
                physical = repo._provider_phase_observation_locked(
                    cur,
                    request_id=str(prior["request_id"]),
                    phase=str(uncertain_attempt["phase"]),
                )
                if not (
                    physical is not None
                    and physical.get("transport_outcome") == "transport_not_submitted"
                    and physical.get("submission_proven") is False
                ):
                    raise TerminalStateError(
                        "The physical Provider result is still uncertain and cannot be replayed."
                    )
                repo._enforce_durable_admission(cur, owner_id, workspace_id)
                repo._apply_reserved_planning_task_lifecycle_fact(
                    cur,
                    session,
                    fact=ResearchStartLifecycleFact.RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED,
                )
                cur.execute(
                    f"""UPDATE research_start_sessions
                          SET state = 'running', result_payload = NULL, user_message = NULL,
                              failure_code = NULL, updated_at = clock_timestamp()
                        WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                          AND state = 'external_result_unknown'
                        RETURNING {SESSION_COLUMNS}""",
                    (session_id, owner_id, workspace_id),
                )
                resumed_session = _row(cur.fetchone())
                if resumed_session is None:
                    raise TerminalStateError()
                request = repo._insert_continuation_request_locked(
                    cur, session=session, prior_request=prior
                )
                repo._copy_attempts_to_continuation_locked(
                    cur,
                    source_request_id=prior["request_id"],
                    target_request_id=request["request_id"],
                    states=("succeeded", "failed"),
                )
                return _reserve_public("reserved", resumed_session, request)

    def mark_external_result_unknown(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        attempt_id: str,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist uncertain external truth rather than allowing an unsafe replay."""
        repo = self._repository
        repo._require_nonempty(request_id=request_id, worker_id=worker_id, attempt_id=attempt_id)
        safe_result = dict(result) if isinstance(result, Mapping) else None
        with repo._connect() as conn:
            with conn.cursor() as cur:
                session, request = repo._lock_path(cur, request_id)
                repo._require_live_lease(session, request, worker_id, lease_epoch)
                attempt = repo._lock_attempt(cur, request_id, attempt_id)
                if attempt["state"] != "started" or attempt["fence_epoch"] != lease_epoch:
                    raise LeaseLostError()
                cur.execute(
                    """UPDATE research_start_attempts a
                          SET state = 'unknown', result_payload = %s::jsonb, finished_at = clock_timestamp()
                          WHERE a.attempt_id = %s AND a.request_id = %s AND a.state = 'started'
                            AND a.fence_epoch = %s AND EXISTS (
                                SELECT 1 FROM research_start_requests r
                                 WHERE r.request_id = a.request_id AND r.session_id = %s
                                   AND r.owner_id = %s AND r.workspace_id = %s
                                   AND r.state = 'leased' AND r.lease_owner = %s AND r.lease_epoch = %s
                                   AND r.lease_expires_at > clock_timestamp()
                            )""",
                    (
                        _json(merge_attempt_result(attempt.get("result_payload"), safe_result)),
                        attempt_id,
                        request_id,
                        lease_epoch,
                        request["session_id"],
                        request["owner_id"],
                        request["workspace_id"],
                        worker_id,
                        lease_epoch,
                    ),
                )
                if cur.rowcount != 1:
                    raise LeaseLostError()
                return repo._set_unknown(
                    cur, session, request, worker_id=worker_id, lease_epoch=lease_epoch
                )

    def reconcile_expired_leases(self) -> dict[str, int]:
        """Reconcile expired work without replaying uncertain Provider calls."""
        repo = self._repository
        reclaimed = 0
        unknown = 0
        reconciled = 0
        with repo._connect() as conn:
            with conn.cursor() as cur:
                while True:
                    cur.execute(
                        f"""SELECT {SESSION_COLUMNS} FROM research_start_sessions s
                              WHERE s.state = 'running' AND EXISTS (
                                  SELECT 1 FROM research_start_requests r
                                   WHERE r.session_id = s.session_id AND r.owner_id = s.owner_id
                                     AND r.workspace_id = s.workspace_id AND r.state = 'leased'
                                     AND r.lease_expires_at <= clock_timestamp()
                              )
                              ORDER BY s.updated_at, s.session_id FOR UPDATE SKIP LOCKED LIMIT 1"""
                    )
                    session = _row(cur.fetchone())
                    if session is None:
                        break
                    cur.execute(
                        """SELECT request_id FROM research_start_requests
                             WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                               AND state = 'leased' AND lease_expires_at <= clock_timestamp()
                             ORDER BY lease_expires_at, request_id FOR UPDATE SKIP LOCKED LIMIT 1""",
                        (session["session_id"], session["owner_id"], session["workspace_id"]),
                    )
                    candidate = _row(cur.fetchone())
                    if candidate is None:
                        continue
                    request = repo._lock_request(
                        cur,
                        candidate["request_id"],
                        session["session_id"],
                        session["owner_id"],
                        session["workspace_id"],
                    )
                    cur.execute(
                        f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
                              WHERE request_id = %s
                                AND (state = 'started' OR (phase = 'research_planner' AND state = 'succeeded'))
                              ORDER BY CASE WHEN state = 'started' THEN 0 ELSE 1 END,
                                       ordinal DESC, started_at DESC, attempt_id DESC
                              LIMIT 1 FOR UPDATE""",
                        (request["request_id"],),
                    )
                    attempt = _row(cur.fetchone())
                    if attempt is None:
                        cur.execute(
                            """UPDATE research_start_requests
                                  SET state = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                                      updated_at = clock_timestamp()
                                  WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
                                    AND state = 'leased' AND lease_expires_at <= clock_timestamp()""",
                            (
                                request["request_id"],
                                request["session_id"],
                                request["owner_id"],
                                request["workspace_id"],
                            ),
                        )
                        reclaimed += cur.rowcount
                        continue
                    receipt = repo._committed_design_receipt(cur, session, request, attempt)
                    if receipt is not None:
                        completed = repo._complete_committed_design_lineage(
                            cur,
                            session,
                            request,
                            attempt,
                            receipt,
                            worker_id=None,
                            lease_epoch=None,
                            require_expired_lease=True,
                            user_message=(
                                "Research design is already confirmed."
                                if receipt.get("researchDesignStatus") == "confirmed"
                                else "Research design is ready to confirm."
                            ),
                        )
                        if completed is not None:
                            reconciled += 1
                            continue
                    if attempt["state"] == "started":
                        physical = repo._provider_phase_observation_locked(
                            cur,
                            request_id=str(request["request_id"]),
                            phase=str(attempt["phase"]),
                        )
                        safe_to_reclaim = bool(
                            physical is None
                            or physical.get("transport_outcome") == "transport_not_submitted"
                            or (
                                physical.get("io_started") is not True
                                and physical.get("submission_proven") is not True
                            )
                        )
                        if safe_to_reclaim:
                            recovery_result = merge_attempt_result(
                                attempt.get("result_payload"),
                                {
                                    "failureCode": "provider_not_submitted",
                                    "recoveryAction": "durable_retry_queued",
                                    "providerPhysicalEvidence": (
                                        None
                                        if physical is None
                                        else {
                                            "attemptId": str(physical["attempt_id"]),
                                            "state": str(physical["execution_state"]),
                                            "transportOutcome": str(physical["transport_outcome"]),
                                            "submissionProven": physical.get("submission_proven"),
                                            "ioStarted": physical.get("io_started"),
                                        }
                                    ),
                                },
                            )
                            cur.execute(
                                """UPDATE research_start_attempts
                                      SET state = 'failed', result_payload = %s::jsonb,
                                          finished_at = clock_timestamp()
                                    WHERE attempt_id = %s AND request_id = %s
                                      AND state = 'started'""",
                                (
                                    _json(recovery_result),
                                    attempt["attempt_id"],
                                    request["request_id"],
                                ),
                            )
                            if cur.rowcount != 1:
                                raise LeaseLostError()
                            cur.execute(
                                """UPDATE research_start_requests
                                      SET state = 'failed', lease_owner = NULL,
                                          lease_expires_at = NULL, result_payload = NULL,
                                          updated_at = clock_timestamp()
                                    WHERE request_id = %s AND session_id = %s
                                      AND owner_id = %s AND workspace_id = %s
                                      AND state = 'leased'
                                      AND lease_expires_at <= clock_timestamp()""",
                                (
                                    request["request_id"],
                                    request["session_id"],
                                    request["owner_id"],
                                    request["workspace_id"],
                                ),
                            )
                            if cur.rowcount != 1:
                                raise LeaseLostError()
                            retry_request = repo._insert_continuation_request_locked(
                                cur, session=session, prior_request=request
                            )
                            repo._copy_attempts_to_continuation_locked(
                                cur,
                                source_request_id=str(request["request_id"]),
                                target_request_id=str(retry_request["request_id"]),
                                states=("succeeded",),
                            )
                            reclaimed += 1
                            continue
                        cur.execute(
                            """UPDATE research_start_attempts a
                                  SET state = 'unknown', finished_at = clock_timestamp()
                                  WHERE a.attempt_id = %s AND a.request_id = %s AND a.state = 'started'
                                    AND EXISTS (
                                        SELECT 1 FROM research_start_requests r
                                         WHERE r.request_id = a.request_id AND r.session_id = %s
                                           AND r.owner_id = %s AND r.workspace_id = %s
                                           AND r.state = 'leased'
                                           AND r.lease_expires_at <= clock_timestamp()
                                    )""",
                            (
                                attempt["attempt_id"],
                                request["request_id"],
                                request["session_id"],
                                request["owner_id"],
                                request["workspace_id"],
                            ),
                        )
                        if cur.rowcount != 1:
                            raise LeaseLostError()
                    elif attempt["state"] != "succeeded":
                        raise TerminalStateError()
                    repo._set_unknown(cur, session, request, return_public=False)
                    unknown += 1
        return {"reclaimed": reclaimed, "unknown": unknown, "reconciled": reconciled}

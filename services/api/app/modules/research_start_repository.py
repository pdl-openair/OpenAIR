"""PostgreSQL authority for durable research-start reservations and attempts.

This module owns no HTTP, worker-loop, provider, task, or artifact behavior.
An external call is permitted only by a newly committed ``begin_attempt``
result.  Known attempts finish independently; request completion is explicit.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from app.config import settings
from app.modules.catalog_execution_binding import apply_catalog_session_authority
from app.modules.research_start_attempt_lease import (
    ATTEMPT_COLUMNS,
    ATTEMPT_PHASES,
    REQUEST_COLUMNS,
    SESSION_COLUMNS,
    ResearchStartAttemptLeaseRepository,
    locate_request as _locate_request_impl,
    lock_attempt as _lock_attempt_impl,
    lock_path as _lock_path_impl,
    lock_request as _lock_request_impl,
    lock_session as _lock_session_impl,
    require_live_lease as _require_live_lease_impl,
    require_nonempty as _require_nonempty_impl,
)
from app.modules.research_start_errors import (
    AttemptAlreadyStartedError,
    AttemptNotFoundError,
    CatalogSessionConflictError,
    FinalizeIdentityConflictError,
    FinalizeInvariantBrokenError,
    InvalidIntentError,
    LeaseLostError,
    RequestConflictError,
    RequestNotFoundError,
    ResearchStartAdmissionFullError,
    ResearchStartError,
    SessionBusyError,
    SessionNotFoundError,
    TaskFinalizeRequiredError,
    TerminalStateError,
)
from app.modules.research_start_finalization import ResearchStartFinalizationOperations
from app.modules.research_start_recovery import ResearchStartRecoveryOperations
from app.modules.research_start_intent import (
    _canonical_intent,
    _durable_fingerprint,
    _fingerprint_normalized,
    _request_fingerprint_matches,
    canonical_fingerprint as _canonical_fingerprint,
)
from app.modules.research_start_projection import (
    UNKNOWN_USER_MESSAGE,
    _attempt_internal,
    _catalog_session_public,
    _json,
    _mapping,
    _planning_progress as _project_planning_progress,
    _reserve_public,
    _row,
    _session_public,
)
from app.modules.research_start_response_evidence import describe_response, merge_attempt_result
from app.modules.research_task_lifecycle import ResearchStartLifecycleFact
from app.modules.research_task_lifecycle_repository import (
    TaskLifecycleWriteConflictError,
    apply_research_start_lifecycle_fact_locked,
)
from app.modules.research_start_admission import enforce_durable_admission

RESEARCH_TASK_STAGE_ORDER = (
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
)
ARTIFACT_TIMESTAMP_FIELDS = frozenset({
    "createdAt",
    "updatedAt",
    "archivedAt",
    "reviewedAt",
    "revisionRequestedAt",
})
STRICT_ISO_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


def canonical_fingerprint(value: Mapping[str, Any]) -> str:
    """Compatibility façade for the extracted pure intent contract."""

    return _canonical_fingerprint(value)


def _planning_progress(row: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility façade for the extracted pure public projection."""

    return _project_planning_progress(row)


def latest_usable_research_design_for_start_completion(designs: Any) -> Any | None:
    """Return the latest Design only when Research Start may close successfully.

    Both active-worker recovery and durable receipt reconciliation must apply
    this exact fail-closed contract.  An older usable Design must never be
    selected after a newer cancelled, superseded, or coverage-incomplete
    version.
    """

    rows = tuple(designs or ())
    if not rows:
        return None
    try:
        latest = max(rows, key=lambda row: int(getattr(row, "version")))
    except (AttributeError, TypeError, ValueError):
        return None

    status = getattr(latest, "status", None)
    status_value = getattr(status, "value", status)
    if status_value not in {"draft", "confirmed"}:
        return None

    coverage = getattr(latest, "coverage_report", None)
    from app.modules.research_design_coverage_confirmation import (
        coverage_allows_confirmation,
    )

    if not coverage_allows_confirmation(coverage):
        return None
    return latest


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _new_session_id() -> str:
    return f"intent_{uuid4().hex[:12]}"


__all__ = (
    "AttemptAlreadyStartedError",
    "AttemptNotFoundError",
    "CatalogSessionConflictError",
    "FinalizeIdentityConflictError",
    "FinalizeInvariantBrokenError",
    "InvalidIntentError",
    "LeaseLostError",
    "RequestConflictError",
    "RequestNotFoundError",
    "ResearchStartAdmissionFullError",
    "ResearchStartError",
    "ResearchStartRepository",
    "SessionBusyError",
    "SessionNotFoundError",
    "TaskFinalizeRequiredError",
    "TerminalStateError",
    "canonical_fingerprint",
    "_fingerprint_normalized",
)


class ResearchStartRepository:
    """Transactional repository with session -> request -> attempt lock order."""

    def __init__(self, connection_factory: Callable[[], Any] | None = None) -> None:
        self._connection_factory = connection_factory

    def for_lease_renewal(self) -> "ResearchStartRepository":
        """Create an independent repository without widening its DB authority."""
        return ResearchStartRepository(connection_factory=self._connection_factory)

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if self._connection_factory is not None:
            conn = self._connection_factory()
        else:
            import psycopg
            from psycopg.rows import dict_row

            conninfo = (
                f"host={settings.postgres_host} port={settings.postgres_port} "
                f"dbname={settings.postgres_database} user={settings.postgres_user}"
            )
            conn = psycopg.connect(
                conninfo,
                password=settings.postgres_password,
                row_factory=dict_row,
                connect_timeout=2,
            )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _require_nonempty(**values: str) -> None:
        _require_nonempty_impl(**values)

    @staticmethod
    def _locate_request(cur: Any, request_id: str) -> dict[str, Any]:
        return _locate_request_impl(cur, request_id)

    @staticmethod
    def _lock_session(cur: Any, session_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        return _lock_session_impl(cur, session_id, owner_id, workspace_id)

    @staticmethod
    def _lock_request(
        cur: Any, request_id: str, session_id: str, owner_id: str, workspace_id: str
    ) -> dict[str, Any]:
        return _lock_request_impl(cur, request_id, session_id, owner_id, workspace_id)

    @staticmethod
    def _lock_attempt(cur: Any, request_id: str, attempt_id: str) -> dict[str, Any]:
        return _lock_attempt_impl(cur, request_id, attempt_id)

    def _lock_path(self, cur: Any, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return _lock_path_impl(cur, request_id)

    @staticmethod
    def _require_live_lease(
        session: Mapping[str, Any], request: Mapping[str, Any], worker_id: str, lease_epoch: int
    ) -> None:
        _require_live_lease_impl(session, request, worker_id, lease_epoch)

    def _attempt_lease_operations(self) -> ResearchStartAttemptLeaseRepository:
        return ResearchStartAttemptLeaseRepository(connect=self._connect, new_id=_new_id)

    def _finalization_operations(self) -> ResearchStartFinalizationOperations:
        return ResearchStartFinalizationOperations(self)

    def _recovery_operations(self) -> ResearchStartRecoveryOperations:
        return ResearchStartRecoveryOperations(self)

    @staticmethod
    def _enforce_durable_admission(cur: Any, owner_id: str, workspace_id: str) -> None:
        """Reconcile stale lineages and gate one reservation atomically."""

        rejection = enforce_durable_admission(
            cur,
            owner_id=owner_id,
            workspace_id=workspace_id,
            runtime_source_version=settings.runtime_source_version,
            enabled=settings.heavy_task_queue_enabled,
            global_limit=settings.heavy_task_global_limit,
            scoped_limit=settings.heavy_task_per_user_limit,
        )
        if rejection is not None:
            raise ResearchStartAdmissionFullError(
                position=rejection.position,
                scope=rejection.scope,
            )

    def reserve(
        self,
        owner_id: str,
        workspace_id: str,
        client_request_id: str,
        intent: Mapping[str, Any],
        *,
        session_id: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        self._require_nonempty(owner_id=owner_id, workspace_id=workspace_id, client_request_id=client_request_id)
        actor_id = created_by if created_by is not None else owner_id
        self._require_nonempty(created_by=actor_id)
        normalized = _canonical_intent(intent)
        intent_session_id = normalized["sessionId"]
        if session_id is not None:
            if re.fullmatch(r"intent_[0-9a-f]{12}", session_id) is None:
                raise InvalidIntentError()
            if intent_session_id is not None and intent_session_id != session_id:
                raise InvalidIntentError()
            normalized["sessionId"] = session_id
        effective_session_id = normalized["sessionId"]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{owner_id}\x1f{workspace_id}\x1f{client_request_id}",),
                )
                cur.execute(
                    """SELECT request_id, session_id FROM research_start_requests
                         WHERE owner_id = %s AND workspace_id = %s AND client_request_id = %s""",
                    (owner_id, workspace_id, client_request_id),
                )
                existing_locator = _row(cur.fetchone())
                if existing_locator is not None:
                    session = self._lock_session(cur, existing_locator["session_id"], owner_id, workspace_id)
                    normalized = apply_catalog_session_authority(normalized, session)
                    request = self._lock_request(
                        cur, existing_locator["request_id"], existing_locator["session_id"], owner_id, workspace_id
                    )
                    if not _request_fingerprint_matches(request, normalized, actor_id):
                        raise RequestConflictError()
                    return _reserve_public("replay", session, request)

                if effective_session_id is not None:
                    session = self._lock_session(cur, effective_session_id, owner_id, workspace_id)
                    normalized = apply_catalog_session_authority(normalized, session)
                    if session["state"] != "running":
                        raise TerminalStateError()
                    cur.execute(
                        """SELECT request_id FROM research_start_requests
                             WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
                               AND state IN ('queued', 'leased')
                             ORDER BY created_at, request_id LIMIT 1 FOR UPDATE""",
                        (effective_session_id, owner_id, workspace_id),
                    )
                    if cur.fetchone() is not None:
                        raise SessionBusyError()
                    self._enforce_durable_admission(cur, owner_id, workspace_id)
                else:
                    self._enforce_durable_admission(cur, owner_id, workspace_id)
                    effective_session_id = _new_session_id()
                    cur.execute(
                        f"""INSERT INTO research_start_sessions(
                               session_id, owner_id, workspace_id, reserved_task_id, reserved_artifact_id
                           ) VALUES (%s, %s, %s, %s, %s) RETURNING {SESSION_COLUMNS}""",
                        (
                            effective_session_id,
                            owner_id,
                            workspace_id,
                            _new_id("task"),
                            _new_id("artifact"),
                        ),
                    )
                    session = _row(cur.fetchone())
                    assert session is not None

                stored_intent = {**normalized, "_createdBy": actor_id}
                fingerprint = _durable_fingerprint(normalized, actor_id)

                request_id = _new_id("rsr")
                cur.execute(
                    f"""INSERT INTO research_start_requests(
                           request_id, session_id, owner_id, workspace_id, client_request_id,
                           fingerprint, runtime_source_version, request_payload
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                       RETURNING {REQUEST_COLUMNS}""",
                    (
                        request_id,
                        effective_session_id,
                        owner_id,
                        workspace_id,
                        client_request_id,
                        fingerprint,
                        settings.runtime_source_version,
                        _json(stored_intent),
                    ),
                )
                request = _row(cur.fetchone())
                assert request is not None
                return _reserve_public("reserved", session, request)

    def reserve_catalog_session(
        self,
        owner_id: str,
        workspace_id: str,
        catalog_intent_id: str,
        selected_resource_id: str,
        selected_resource_version: str,
        selected_capability_id: str,
    ) -> dict[str, Any]:
        self._require_nonempty(
            owner_id=owner_id,
            workspace_id=workspace_id,
            catalog_intent_id=catalog_intent_id,
            selected_resource_id=selected_resource_id,
            selected_resource_version=selected_resource_version,
            selected_capability_id=selected_capability_id,
        )
        if re.fullmatch(r"intent_[0-9a-f]{12}", catalog_intent_id) is None:
            raise InvalidIntentError()

        expected_snapshot = (
            selected_resource_id,
            selected_resource_version,
            selected_capability_id,
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO research_start_sessions(
                           session_id, owner_id, workspace_id, reserved_task_id, reserved_artifact_id,
                           catalog_intent_id, selected_resource_id, selected_resource_version,
                           selected_capability_id
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (owner_id, workspace_id, catalog_intent_id)
                           WHERE catalog_intent_id IS NOT NULL DO NOTHING
                       RETURNING {SESSION_COLUMNS}""",
                    (
                        _new_session_id(),
                        owner_id,
                        workspace_id,
                        _new_id("task"),
                        _new_id("artifact"),
                        catalog_intent_id,
                        selected_resource_id,
                        selected_resource_version,
                        selected_capability_id,
                    ),
                )
                session = _row(cur.fetchone())
                if session is None:
                    cur.execute(
                        f"""SELECT {SESSION_COLUMNS} FROM research_start_sessions
                              WHERE owner_id = %s AND workspace_id = %s AND catalog_intent_id = %s
                              FOR UPDATE""",
                        (owner_id, workspace_id, catalog_intent_id),
                    )
                    session = _row(cur.fetchone())
                if session is None:
                    raise CatalogSessionConflictError()
                persisted_snapshot = (
                    session["selected_resource_id"],
                    session["selected_resource_version"],
                    session["selected_capability_id"],
                )
                if persisted_snapshot != expected_snapshot:
                    raise CatalogSessionConflictError()
                return _catalog_session_public(session)

    def get_scoped_session(self, owner_id: str, workspace_id: str, session_id: str) -> dict[str, Any]:
        self._require_nonempty(owner_id=owner_id, workspace_id=workspace_id, session_id=session_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s.session_id, s.owner_id, s.workspace_id, s.state,
                               s.reserved_task_id, s.reserved_artifact_id, s.result_payload,
                               s.user_message, s.failure_code, s.thinking_transcript, s.catalog_intent_id,
                               s.selected_resource_id, s.selected_resource_version,
                               s.selected_capability_id, s.created_at, s.updated_at,
                               r.state AS request_state, r.created_at AS request_created_at,
                               r.updated_at AS request_updated_at,
                               a.phase AS attempt_phase, a.ordinal AS attempt_ordinal,
                               a.state AS attempt_state, a.result_payload AS attempt_result_payload,
                               a.started_at AS attempt_started_at, a.finished_at AS attempt_finished_at,
                               GREATEST(
                                   0,
                                   FLOOR(EXTRACT(EPOCH FROM (
                                       CASE WHEN s.state = 'running' THEN clock_timestamp() ELSE s.updated_at END
                                       - s.created_at
                                   )))::bigint
                               ) AS elapsed_seconds,
                               GREATEST(
                                   0,
                                   FLOOR(EXTRACT(EPOCH FROM (
                                       clock_timestamp() - COALESCE(r.created_at, s.created_at)
                                   )))::bigint
                               ) AS request_age_seconds,
                               EXISTS (
                                   SELECT 1 FROM research_start_requests active
                                    WHERE active.state = 'leased'
                                      AND active.lease_expires_at > clock_timestamp()
                               ) AS has_live_research_start_lease
                          FROM research_start_sessions s
                          LEFT JOIN LATERAL (
                              SELECT request_id, state, created_at, updated_at
                                FROM research_start_requests
                               WHERE session_id = s.session_id AND owner_id = s.owner_id
                                 AND workspace_id = s.workspace_id
                               ORDER BY created_at DESC, request_id DESC LIMIT 1
                          ) r ON TRUE
                          LEFT JOIN LATERAL (
                              SELECT phase, ordinal, state, result_payload, started_at, finished_at
                                FROM research_start_attempts
                               WHERE request_id = r.request_id
                               ORDER BY ordinal DESC, started_at DESC, attempt_id DESC LIMIT 1
                          ) a ON TRUE
                         WHERE s.session_id = %s AND s.owner_id = %s AND s.workspace_id = %s""",
                    (session_id, owner_id, workspace_id),
                )
                session = _row(cur.fetchone())
                if session is None:
                    raise SessionNotFoundError()
                return _session_public(session, attempt_phase=session.get("attempt_phase"))

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        not_before: datetime | None = None,
    ) -> dict[str, Any] | None:
        return self._attempt_lease_operations().claim(
            worker_id,
            lease_seconds=lease_seconds,
            not_before=not_before,
        )

    def renew(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        lease_seconds: int = 60,
        query_timeout_ms: int = 2000,
    ) -> dict[str, Any]:
        return self._attempt_lease_operations().renew(
            request_id,
            worker_id,
            lease_epoch,
            lease_seconds=lease_seconds,
            query_timeout_ms=query_timeout_ms,
        )

    def load_execution_context(self, request_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
        return self._attempt_lease_operations().load_execution_context(
            request_id,
            worker_id,
            lease_epoch,
        )

    def begin_attempt(
        self, request_id: str, worker_id: str, lease_epoch: int, phase: str, ordinal: int
    ) -> dict[str, Any]:
        return self._attempt_lease_operations().begin_attempt(
            request_id,
            worker_id,
            lease_epoch,
            phase,
            ordinal,
        )

    def finish_attempt(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        attempt_id: str,
        *,
        succeeded: bool,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_nonempty(request_id=request_id, worker_id=worker_id, attempt_id=attempt_id)
        target = "succeeded" if succeeded else "failed"
        with self._connect() as conn:
            with conn.cursor() as cur:
                session, request = self._lock_path(cur, request_id)
                self._require_live_lease(session, request, worker_id, lease_epoch)
                attempt = self._lock_attempt(cur, request_id, attempt_id)
                if attempt["fence_epoch"] != lease_epoch:
                    raise LeaseLostError()
                if attempt["state"] != "started":
                    raise TerminalStateError()
                cur.execute(
                    f"""UPDATE research_start_attempts a
                          SET state = %s, result_payload = %s::jsonb, finished_at = clock_timestamp()
                          WHERE a.attempt_id = %s AND a.request_id = %s AND a.state = 'started'
                            AND a.fence_epoch = %s AND EXISTS (
                                SELECT 1 FROM research_start_requests r
                                 WHERE r.request_id = a.request_id AND r.session_id = %s
                                   AND r.owner_id = %s AND r.workspace_id = %s
                                   AND r.state = 'leased' AND r.lease_owner = %s AND r.lease_epoch = %s
                                   AND r.lease_expires_at > clock_timestamp()
                            ) RETURNING {ATTEMPT_COLUMNS}""",
                    (
                        target,
                        _json(merge_attempt_result(attempt.get("result_payload"), result)),
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
                finished = _row(cur.fetchone())
                if finished is None:
                    raise LeaseLostError()
                cur.execute(
                    """UPDATE research_start_requests SET updated_at = clock_timestamp()
                         WHERE request_id = %s AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                           AND lease_expires_at > clock_timestamp()""",
                    (request_id, worker_id, lease_epoch),
                )
                if cur.rowcount != 1:
                    raise LeaseLostError()
                return _attempt_internal(finished, permitted=False, replay=False)

    @staticmethod
    def _provider_phase_observation_locked(
        cur: Any,
        *,
        request_id: str,
        phase: str,
    ) -> dict[str, Any] | None:
        """Read body-free physical truth for one Research Start phase.

        The row is read while the Session/Request/Attempt path is locked by the
        caller.  Multiple physical rows can exist inside a Provider adapter's
        bounded model/transport fallback, so the newest physical ordinal is the
        decisive observation for settlement.
        """
        cur.execute(
            """SELECT attempt_id, execution_state, transport_outcome,
                      provider_request_id, provider_response_id, http_status,
                      io_started, submission_proven, operation_identity,
                      logical_attempt_ordinal
                 FROM openair_provider_http_attempts
                WHERE parent_kind = 'research_start' AND parent_id = %s
                  AND evidence_kind = 'provider_submission'
                  AND operation_identity = %s
                ORDER BY attempt_number DESC
                LIMIT 1
                FOR UPDATE""",
            (request_id, f"research_start:{phase}"),
        )
        return _row(cur.fetchone())

    def settle_provider_phase_failure(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        attempt_id: str,
        *,
        failure_result: Mapping[str, Any],
    ) -> str:
        """Atomically settle a failed Provider phase from all authority facts.

        A response-observed/local failure is a known failed logical attempt and
        can never cause another physical call.  An in-flight/uncertain physical
        attempt becomes recoverable unknown.  Absence of I/O evidence is a
        proven local/pre-submit failure because production Provider execution is
        required to reserve physical evidence before crossing the I/O boundary.
        An authoritative ResearchDesign receipt always wins first.
        """
        self._require_nonempty(
            request_id=request_id,
            worker_id=worker_id,
            attempt_id=attempt_id,
        )
        if not isinstance(failure_result, Mapping):
            raise ValueError("failure_result must be a JSON object")
        with self._connect() as conn:
            with conn.cursor() as cur:
                session, request = self._lock_path(cur, request_id)
                self._require_live_lease(session, request, worker_id, lease_epoch)
                attempt = self._lock_attempt(cur, request_id, attempt_id)
                if attempt["fence_epoch"] != lease_epoch:
                    raise LeaseLostError()
                if attempt["state"] != "started":
                    raise TerminalStateError()

                if attempt["phase"] == "research_planner":
                    receipt = self._committed_design_receipt(cur, session, request, attempt)
                    if receipt is not None:
                        completed = self._complete_committed_design_lineage(
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
                                else "Research design is ready to confirm."
                            ),
                        )
                        if completed is None:
                            raise FinalizeInvariantBrokenError()
                        return "task_handoff"

                physical = self._provider_phase_observation_locked(
                    cur,
                    request_id=request_id,
                    phase=str(attempt["phase"]),
                )
                persisted = dict(failure_result)
                if physical is not None:
                    persisted["providerPhysicalEvidence"] = {
                        "attemptId": str(physical["attempt_id"]),
                        "state": str(physical["execution_state"]),
                        "transportOutcome": str(physical["transport_outcome"]),
                        "submissionProven": physical.get("submission_proven"),
                        "ioStarted": physical.get("io_started"),
                    }
                physical_uncertain = bool(
                    physical is not None
                    and str(physical.get("execution_state")) in {"running", "unknown"}
                    and physical.get("io_started") is True
                    and physical.get("submission_proven") is not False
                )
                if physical_uncertain:
                    cur.execute(
                        """UPDATE research_start_attempts
                              SET state = 'unknown', result_payload = %s::jsonb,
                                  finished_at = clock_timestamp()
                            WHERE attempt_id = %s AND request_id = %s
                              AND state = 'started' AND fence_epoch = %s""",
                        (
                            _json(merge_attempt_result(attempt.get("result_payload"), persisted)),
                            attempt_id,
                            request_id,
                            lease_epoch,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise LeaseLostError()
                    self._set_unknown(
                        cur,
                        session,
                        request,
                        worker_id=worker_id,
                        lease_epoch=lease_epoch,
                        return_public=False,
                    )
                    return "external_result_unknown"

                cur.execute(
                    """UPDATE research_start_attempts
                          SET state = 'failed', result_payload = %s::jsonb,
                              finished_at = clock_timestamp()
                        WHERE attempt_id = %s AND request_id = %s
                          AND state = 'started' AND fence_epoch = %s""",
                    (
                        _json(merge_attempt_result(attempt.get("result_payload"), persisted)),
                        attempt_id,
                        request_id,
                        lease_epoch,
                    ),
                )
                if cur.rowcount != 1:
                    raise LeaseLostError()
                return (
                    "response_observed"
                    if physical is not None and physical.get("submission_proven") is True
                    else "not_submitted"
                )

    @staticmethod
    def _assert_no_started_attempt(cur: Any, request_id: str) -> None:
        cur.execute(
            """SELECT attempt_id FROM research_start_attempts
                 WHERE request_id = %s AND state = 'started' ORDER BY attempt_id LIMIT 1 FOR UPDATE""",
            (request_id,),
        )
        if cur.fetchone() is not None:
            raise AttemptAlreadyStartedError()

    def complete_request(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        result: Mapping[str, Any],
        user_message: str,
    ) -> dict[str, Any]:
        self._require_nonempty(request_id=request_id, worker_id=worker_id, user_message=user_message)
        if not isinstance(result, Mapping):
            raise ValueError("result must be a JSON object")
        if result.get("outcome") == "research_task" or result.get("taskId"):
            raise TaskFinalizeRequiredError()
        payload = _json(result)
        with self._connect() as conn:
            with conn.cursor() as cur:
                session, request = self._lock_path(cur, request_id)
                self._require_live_lease(session, request, worker_id, lease_epoch)
                self._assert_no_started_attempt(cur, request_id)
                self._assert_task_finalize_not_required(cur, request_id)
                cur.execute(
                    """UPDATE research_start_requests
                          SET state = 'succeeded', lease_owner = NULL, lease_expires_at = NULL,
                              result_payload = %s::jsonb, updated_at = clock_timestamp()
                          WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
                            AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                            AND lease_expires_at > clock_timestamp()""",
                    (
                        payload,
                        request_id,
                        request["session_id"],
                        request["owner_id"],
                        request["workspace_id"],
                        worker_id,
                        lease_epoch,
                    ),
                )
                if cur.rowcount != 1:
                    raise LeaseLostError()
                cur.execute(
                    f"""UPDATE research_start_sessions
                          SET state = 'completed', result_payload = %s::jsonb, user_message = %s,
                              failure_code = NULL, updated_at = clock_timestamp()
                          WHERE session_id = %s AND owner_id = %s AND workspace_id = %s AND state = 'running'
                          RETURNING {SESSION_COLUMNS}""",
                    (
                        payload,
                        user_message,
                        session["session_id"],
                        session["owner_id"],
                        session["workspace_id"],
                    ),
                )
                completed = _row(cur.fetchone())
                if completed is None:
                    raise TerminalStateError()
                return _session_public(completed)

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
        return self._finalization_operations().complete_existing_task_request(
            request_id,
            worker_id,
            lease_epoch,
            task_id=task_id,
            result=result,
            user_message=user_message,
        )

    @staticmethod
    def _committed_design_receipt(
        cur: Any,
        session: Mapping[str, Any],
        request: Mapping[str, Any],
        attempt: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Verify a durable Task/ResearchDesign lineage without provider replay."""

        if attempt.get("phase") != "research_planner" or int(attempt.get("ordinal") or 0) not in {1, 2}:
            return None
        cur.execute(
            """SELECT payload FROM openair_tasks
                 WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                 FOR UPDATE""",
            (session["reserved_task_id"], session["owner_id"], session["workspace_id"]),
        )
        task_row = _row(cur.fetchone())
        if task_row is None:
            return None
        payload = _mapping(task_row.get("payload"))
        if payload is None:
            return None
        try:
            from app.models.research_orchestration import (
                PlanningAttemptStatus,
                ResearchTaskV12,
            )
            from app.modules.research_orchestration_repository import _extract_contract_payload

            task = ResearchTaskV12.model_validate(_extract_contract_payload(payload))
        except (ImportError, KeyError, TypeError, ValueError):
            return None
        if task.task_id != session["reserved_task_id"]:
            return None
        design_request_id = str(request["request_id"])
        if int(attempt["ordinal"]) == 2:
            design_request_id = f"{design_request_id}:semantic-repair:2"
        planning_attempt = next(
            (
                row
                for row in task.planning_attempts
                if row.request_id == design_request_id
                and row.status == PlanningAttemptStatus.COMPLETED
            ),
            None,
        )
        if planning_attempt is None:
            return None
        # Select the canonical latest Design before checking receipt lineage.
        # Filtering by planning attempt first can resurrect an older draft after
        # a later revision was cancelled or superseded.
        design = latest_usable_research_design_for_start_completion(
            task.research_design_versions
        )
        if design is None:
            return None
        coverage = design.coverage_report
        if (
            coverage is None
            or coverage.planning_attempt_id != planning_attempt.planning_attempt_id
        ):
            return None

        task_provider_call_ref = planning_attempt.planner_provider_call_ref
        attempt_state = str(attempt.get("state") or "")
        design_status = str(getattr(design.status, "value", design.status))
        design_ref = {
            "researchDesignId": design.research_design_id,
            "version": design.version,
        }

        if task_provider_call_ref is None:
            return None

        if attempt_state == "succeeded":
            attempt_result = _mapping(attempt.get("result_payload"))
            attempt_provider_call_ref = (
                attempt_result.get("providerCallRef")
                if attempt_result is not None
                else None
            )
            if (
                not isinstance(attempt_provider_call_ref, str)
                or not attempt_provider_call_ref.strip()
                or attempt_provider_call_ref != task_provider_call_ref
            ):
                return None

        return {
            "providerCallRef": task_provider_call_ref,
            "taskId": task.task_id,
            "researchDesignRef": design_ref,
            "researchDesignStatus": design_status,
            "reconciledFromCommittedDesign": True,
        }

    @staticmethod
    def _complete_committed_design_lineage(
        cur: Any,
        session: Mapping[str, Any],
        request: Mapping[str, Any],
        attempt: Mapping[str, Any],
        receipt: Mapping[str, Any],
        *,
        worker_id: str | None,
        lease_epoch: int | None,
        require_expired_lease: bool,
        user_message: str,
    ) -> dict[str, Any] | None:
        attempt_payload = _json(merge_attempt_result(attempt.get("result_payload"), receipt))
        if attempt["state"] == "started":
            cur.execute(
                """UPDATE research_start_attempts
                      SET state = 'succeeded', result_payload = %s::jsonb,
                          finished_at = clock_timestamp()
                    WHERE attempt_id = %s AND request_id = %s AND state = 'started'
                      AND fence_epoch = %s""",
                (
                    attempt_payload,
                    attempt["attempt_id"],
                    request["request_id"],
                    attempt["fence_epoch"],
                ),
            )
            if cur.rowcount != 1:
                raise LeaseLostError()
        elif attempt["state"] != "succeeded":
            raise TerminalStateError()

        result = {
            "outcome": "research_task",
            "sessionId": session["session_id"],
            "taskId": session["reserved_task_id"],
            "authoritativeReceipt": dict(receipt),
        }
        result_payload = _json(result)
        lease_clause = "AND lease_expires_at <= clock_timestamp()" if require_expired_lease else (
            "AND lease_owner = %s AND lease_epoch = %s AND lease_expires_at > clock_timestamp()"
        )
        params: list[Any] = [
            result_payload,
            request["request_id"],
            request["session_id"],
            request["owner_id"],
            request["workspace_id"],
        ]
        if not require_expired_lease:
            params.extend([worker_id, lease_epoch])
        cur.execute(
            f"""UPDATE research_start_requests
                   SET state = 'succeeded', lease_owner = NULL, lease_expires_at = NULL,
                       result_payload = %s::jsonb, updated_at = clock_timestamp()
                 WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
                   AND state = 'leased' {lease_clause}""",
            params,
        )
        if cur.rowcount != 1:
            raise LeaseLostError()
        cur.execute(
            f"""UPDATE research_start_sessions
                  SET state = 'completed', result_payload = %s::jsonb, user_message = %s,
                      failure_code = NULL, updated_at = clock_timestamp()
                  WHERE session_id = %s AND owner_id = %s AND workspace_id = %s AND state = 'running'
                  RETURNING {SESSION_COLUMNS}""",
            (
                result_payload,
                user_message,
                session["session_id"],
                session["owner_id"],
                session["workspace_id"],
            ),
        )
        completed = _row(cur.fetchone())
        if completed is None:
            raise TerminalStateError()
        return _session_public(completed)

    @staticmethod
    def _close_authoritative_finalize_failure(
        cur: Any,
        session: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        worker_id: str,
        lease_epoch: int,
        reason: str,
    ) -> dict[str, Any]:
        """Fail closed without mutating a newer or cancelled ResearchDesign Task."""

        cur.execute(
            """UPDATE research_start_requests
                  SET state = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                      result_payload = NULL, updated_at = clock_timestamp()
                WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
                  AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                  AND lease_expires_at > clock_timestamp()""",
            (
                request["request_id"],
                request["session_id"],
                request["owner_id"],
                request["workspace_id"],
                worker_id,
                lease_epoch,
            ),
        )
        if cur.rowcount != 1:
            raise LeaseLostError()
        cur.execute(
            f"""UPDATE research_start_sessions
                  SET state = 'failed', result_payload = NULL,
                      user_message = %s, failure_code = 'provider_contract_invalid',
                      updated_at = clock_timestamp()
                WHERE session_id = %s AND owner_id = %s AND workspace_id = %s AND state = 'running'
                RETURNING {SESSION_COLUMNS}""",
            (
                f"The durable research design receipt could not be verified ({reason}).",
                session["session_id"],
                session["owner_id"],
                session["workspace_id"],
            ),
        )
        failed = _row(cur.fetchone())
        if failed is None:
            raise TerminalStateError()
        return _session_public(failed)

    def _finalize_authoritative_receipt_transaction(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        user_message: str,
        require_succeeded_attempt: bool,
    ) -> dict[str, Any]:
        return self._finalization_operations().finalize_authoritative_receipt_transaction(
            request_id,
            worker_id,
            lease_epoch,
            user_message=user_message,
            require_succeeded_attempt=require_succeeded_attempt,
        )

    def finalize_from_authoritative_receipt(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        user_message: str,
    ) -> dict[str, Any]:
        return self._finalization_operations().finalize_from_authoritative_receipt(
            request_id,
            worker_id,
            lease_epoch,
            user_message=user_message,
        )

    def reconcile_committed_design_request(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        user_message: str,
    ) -> dict[str, Any] | None:
        return self._finalization_operations().reconcile_committed_design_request(
            request_id,
            worker_id,
            lease_epoch,
            user_message=user_message,
        )

    @staticmethod
    def _assert_task_finalize_not_required(cur: Any, request_id: str) -> None:
        cur.execute(
            """SELECT phase, state, result_payload FROM research_start_attempts
                 WHERE request_id = %s ORDER BY phase, ordinal FOR UPDATE""",
            (request_id,),
        )
        for raw in cur.fetchall():
            row = _row(raw) or {}
            if row.get("state") != "succeeded":
                continue
            if row.get("phase") == "research_planner":
                raise TaskFinalizeRequiredError()

    @staticmethod
    def _finalize_result(session: Mapping[str, Any]) -> dict[str, str]:
        return {
            "outcome": "research_task",
            "sessionId": str(session["session_id"]),
            "taskId": str(session["reserved_task_id"]),
        }

    @staticmethod
    def _assert_finalize_payload(
        session: Mapping[str, Any], task: Mapping[str, Any], artifact: Mapping[str, Any],
        result: Mapping[str, Any], created_by: str | None = None,
    ) -> dict[str, str]:
        expected = ResearchStartRepository._finalize_result(session)
        expected_created_by = session["owner_id"] if created_by is None else created_by
        if dict(result) != expected:
            raise FinalizeInvariantBrokenError()
        task_id = session["reserved_task_id"]
        artifact_id = session["reserved_artifact_id"]
        scope = (session["owner_id"], session["workspace_id"])
        if (
            task.get("taskId") != task_id
            or task.get("ownerId") != scope[0]
            or task.get("workspaceId") != scope[1]
            or task.get("createdBy") != expected_created_by
            or task.get("domain") != "research_task"
            or task.get("status") != "draft"
            or task.get("creationStatus") != "awaiting_confirmation"
            or task.get("resultSource") != "live"
            or task.get("currentStage") != "problem_modeling"
            or task.get("artifacts") != [artifact]
        ):
            raise FinalizeInvariantBrokenError()
        details = task.get("stageDetails")
        if task.get("stages") != list(RESEARCH_TASK_STAGE_ORDER) or not isinstance(details, list):
            raise FinalizeInvariantBrokenError()
        if [row.get("stageKey") if isinstance(row, Mapping) else None for row in details] != list(RESEARCH_TASK_STAGE_ORDER):
            raise FinalizeInvariantBrokenError()
        if details[0].get("artifactRefs") != [artifact_id] or any(
            artifact_id in (row.get("artifactRefs") or []) for row in details[1:]
        ):
            raise FinalizeInvariantBrokenError()
        if (
            artifact.get("id") != artifact_id
            or artifact.get("taskId") != task_id
            or artifact.get("sourceTaskId") != task_id
            or artifact.get("ownerId") != scope[0]
            or artifact.get("workspaceId") != scope[1]
            or artifact.get("createdBy") != expected_created_by
            or artifact.get("kind") != "research_start_draft"
            or artifact.get("origin") != "generated"
            or artifact.get("storageType") != "inline"
            or not isinstance(artifact.get("inlineContent"), str)
            or not artifact["inlineContent"]
            or artifact.get("sourceCapabilityCallId") is not None
            or artifact.get("artifactState") not in {"needs_input", "needs_review"}
            or "createdAt" not in artifact
            or any(
                key not in artifact
                for key in (
                    "structuredResearchStart",
                    "researchStartConfirmation",
                    "missingInputs",
                    "nonCitableBoundaries",
                    "downstreamInput",
                )
            )
        ):
            raise FinalizeInvariantBrokenError()
        ResearchStartRepository._parse_artifact_timestamp(artifact["createdAt"])
        return expected

    @staticmethod
    def _assert_exact_succeeded_attempts(cur: Any, request_id: str) -> None:
        cur.execute(
            """SELECT phase, ordinal, state FROM research_start_attempts
                 WHERE request_id = %s ORDER BY phase, ordinal FOR UPDATE""",
            (request_id,),
        )
        rows = [_row(row) or {} for row in cur.fetchall()]
        if any(row.get("state") in {"started", "unknown"} for row in rows):
            raise FinalizeInvariantBrokenError()
        succeeded = [row for row in rows if row.get("state") == "succeeded"]
        if len(succeeded) != 2 or {row.get("phase") for row in succeeded} != set(ATTEMPT_PHASES):
            raise FinalizeInvariantBrokenError()

    @staticmethod
    def _load_finalize_replay_rows(cur: Any, session: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        cur.execute(
            """SELECT payload FROM openair_tasks
                 WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                   AND status <> 'deleted' FOR UPDATE""",
            (session["reserved_task_id"], session["owner_id"], session["workspace_id"]),
        )
        task_row = _row(cur.fetchone())
        if task_row is None:
            raise FinalizeInvariantBrokenError()
        cur.execute(
            """SELECT to_jsonb(a) AS artifact_row FROM openair_artifacts a
                 WHERE a.artifact_id = %s AND a.source_task_id = %s AND a.owner_id = %s AND a.workspace_id = %s
                 FOR UPDATE""",
            (session["reserved_artifact_id"], session["reserved_task_id"], session["owner_id"], session["workspace_id"]),
        )
        artifact_record = _row(cur.fetchone())
        if artifact_record is None or not isinstance(artifact_record.get("artifact_row"), Mapping):
            raise FinalizeInvariantBrokenError()
        task = task_row.get("payload")
        if isinstance(task, str):
            try:
                task = json.loads(task)
            except (TypeError, ValueError) as exc:
                raise FinalizeInvariantBrokenError() from exc
        if not isinstance(task, dict):
            raise FinalizeInvariantBrokenError()
        from app.modules.artifact_repository import _row_to_dict

        artifact = _row_to_dict(dict(artifact_record["artifact_row"]))
        return task, artifact

    @staticmethod
    def _parse_artifact_timestamp(value: Any) -> datetime:
        if not isinstance(value, str) or STRICT_ISO_INSTANT.fullmatch(value) is None:
            raise FinalizeInvariantBrokenError()
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        except ValueError as exc:
            raise FinalizeInvariantBrokenError() from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise FinalizeInvariantBrokenError()
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _artifact_replay_value_matches(key: str, embedded: Any, catalog: Any) -> bool:
        if key not in ARTIFACT_TIMESTAMP_FIELDS:
            return embedded == catalog
        return (
            ResearchStartRepository._parse_artifact_timestamp(embedded)
            == ResearchStartRepository._parse_artifact_timestamp(catalog)
        )

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
        return self._finalization_operations().finalize_task_request(
            request_id,
            worker_id,
            lease_epoch,
            task=task,
            artifact=artifact,
            result=result,
            user_message=user_message,
        )

    def fail_request(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        failure_code: str,
        user_message: str,
    ) -> dict[str, Any]:
        self._require_nonempty(
            request_id=request_id,
            worker_id=worker_id,
            failure_code=failure_code,
            user_message=user_message,
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                session, request = self._lock_path(cur, request_id)
                self._require_live_lease(session, request, worker_id, lease_epoch)
                # A legacy failure can arrive after the Planner has
                # durably committed a validated Design but before its normal
                # finalizer closes the session.  The Design receipt is the
                # only completion authority in that window: never let this
                # secondary failure downgrade it.
                cur.execute(
                    f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
                          WHERE request_id = %s AND phase = 'research_planner'
                            AND state IN ('started', 'succeeded')
                          ORDER BY ordinal DESC, started_at DESC, attempt_id DESC
                          LIMIT 1 FOR UPDATE""",
                    (request_id,),
                )
                planner_attempt = _row(cur.fetchone())
                if planner_attempt is not None:
                    receipt = self._committed_design_receipt(
                        cur,
                        session,
                        request,
                        planner_attempt,
                    )
                    if receipt is not None:
                        completed = self._complete_committed_design_lineage(
                            cur,
                            session,
                            request,
                            planner_attempt,
                            receipt,
                            worker_id=worker_id,
                            lease_epoch=lease_epoch,
                            require_expired_lease=False,
                            user_message=(
                                "Research design is already confirmed."
                                if receipt.get("researchDesignStatus") == "confirmed"
                                else "Research design is ready to confirm."
                            ),
                        )
                        if completed is None:
                            raise FinalizeInvariantBrokenError()
                        return completed
                self._assert_no_started_attempt(cur, request_id)
                cur.execute(
                    """UPDATE research_start_requests
                          SET state = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                              result_payload = NULL, updated_at = clock_timestamp()
                          WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
                            AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                            AND lease_expires_at > clock_timestamp()""",
                    (
                        request_id,
                        request["session_id"],
                        request["owner_id"],
                        request["workspace_id"],
                        worker_id,
                        lease_epoch,
                    ),
                )
                if cur.rowcount != 1:
                    raise LeaseLostError()
                self._apply_reserved_planning_task_lifecycle_fact(
                    cur,
                    session,
                    fact=ResearchStartLifecycleFact.PLANNING_FAILED,
                )
                cur.execute(
                    f"""UPDATE research_start_sessions
                          SET state = 'failed', result_payload = NULL, user_message = %s,
                              failure_code = %s, updated_at = clock_timestamp()
                          WHERE session_id = %s AND owner_id = %s AND workspace_id = %s AND state = 'running'
                          RETURNING {SESSION_COLUMNS}""",
                    (
                        user_message,
                        failure_code,
                        session["session_id"],
                        session["owner_id"],
                        session["workspace_id"],
                    ),
                )
                failed = _row(cur.fetchone())
                if failed is None:
                    raise TerminalStateError()
                return _session_public(failed)

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
        """Atomically resolve one explicit Planner HTTP failure.

        The repository owns the durable state transition, not HTTP recovery
        classification. The caller supplies the bounded automatic-recovery
        budget from the recovery-policy authority. The persisted attempt stays
        a known failed attempt in every case.
        """
        return self._recovery_operations().resolve_known_planner_http_failure(
            request_id,
            worker_id,
            lease_epoch,
            attempt_id,
            failure_result=failure_result,
            automatic_recovery_limit=automatic_recovery_limit,
            terminal_user_message=terminal_user_message,
        )

    @staticmethod
    def _finish_started_attempt_locked(
        cur: Any,
        *,
        session: Mapping[str, Any],
        request: Mapping[str, Any],
        worker_id: str,
        lease_epoch: int,
        attempt_id: str,
        result: Mapping[str, Any],
    ) -> None:
        cur.execute(
            """UPDATE research_start_attempts a
                  SET state = 'failed', result_payload = %s::jsonb, finished_at = clock_timestamp()
                  WHERE a.attempt_id = %s AND a.request_id = %s AND a.state = 'started'
                    AND a.fence_epoch = %s AND EXISTS (
                        SELECT 1 FROM research_start_requests r
                         WHERE r.request_id = a.request_id AND r.session_id = %s
                           AND r.owner_id = %s AND r.workspace_id = %s
                           AND r.state = 'leased' AND r.lease_owner = %s AND r.lease_epoch = %s
                           AND r.lease_expires_at > clock_timestamp()
                    )""",
            (
                _json(dict(result)),
                attempt_id,
                request["request_id"],
                lease_epoch,
                session["session_id"],
                session["owner_id"],
                session["workspace_id"],
                worker_id,
                lease_epoch,
            ),
        )
        if cur.rowcount != 1:
            raise LeaseLostError()

    @staticmethod
    def _close_leased_request_as_failed_locked(
        cur: Any,
        *,
        session: Mapping[str, Any],
        request: Mapping[str, Any],
        worker_id: str,
        lease_epoch: int,
    ) -> None:
        cur.execute(
            """UPDATE research_start_requests
                  SET state = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                      result_payload = NULL, updated_at = clock_timestamp()
                WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
                  AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                  AND lease_expires_at > clock_timestamp()""",
            (
                request["request_id"],
                session["session_id"],
                session["owner_id"],
                session["workspace_id"],
                worker_id,
                lease_epoch,
            ),
        )
        if cur.rowcount != 1:
            raise LeaseLostError()

    @staticmethod
    def _insert_continuation_request_locked(
        cur: Any,
        *,
        session: Mapping[str, Any],
        prior_request: Mapping[str, Any],
    ) -> dict[str, Any]:
        intent = prior_request.get("request_payload")
        if isinstance(intent, str):
            intent = json.loads(intent)
        if not isinstance(intent, Mapping):
            raise InvalidIntentError()
        request_id = _new_id("rsr")
        cur.execute(
            f"""INSERT INTO research_start_requests(
                   request_id, session_id, owner_id, workspace_id, client_request_id,
                   fingerprint, runtime_source_version, request_payload
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
               RETURNING {REQUEST_COLUMNS}""",
            (
                request_id,
                session["session_id"],
                session["owner_id"],
                session["workspace_id"],
                _new_id("retry"),
                prior_request["fingerprint"],
                settings.runtime_source_version,
                _json(dict(intent)),
            ),
        )
        request = _row(cur.fetchone())
        if request is None:
            raise TerminalStateError()
        return request

    @staticmethod
    def _copy_attempts_to_continuation_locked(
        cur: Any,
        *,
        source_request_id: str,
        target_request_id: str,
        states: tuple[str, ...],
    ) -> None:
        if not states:
            return
        cur.execute(
            """SELECT phase, ordinal, state, fence_epoch, result_payload, response_body,
                      started_at, finished_at
                 FROM research_start_attempts
                WHERE request_id = %s AND state = ANY(%s)
                ORDER BY phase, ordinal, started_at, attempt_id""",
            (source_request_id, list(states)),
        )
        for raw in cur.fetchall():
            completed = _row(raw)
            if completed is None:
                continue
            cur.execute(
                """INSERT INTO research_start_attempts(
                       attempt_id, request_id, phase, ordinal, state, fence_epoch,
                       result_payload, response_body, started_at, finished_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)""",
                (
                    _new_id("rsa"),
                    target_request_id,
                    completed["phase"],
                    completed["ordinal"],
                    completed["state"],
                    completed["fence_epoch"],
                    _json(completed.get("result_payload")),
                    completed.get("response_body"),
                    completed["started_at"],
                    completed["finished_at"],
                ),
            )

    def retry_external_result_unknown(
        self, owner_id: str, workspace_id: str, session_id: str
    ) -> dict[str, Any]:
        """Resume only when physical truth later proves no submission occurred.

        A user action cannot waive physical-submission idempotency.  A genuinely
        uncertain or response-observed Provider attempt therefore remains under
        reconciliation responsibility and is never converted into a fresh call.
        """
        return self._recovery_operations().retry_external_result_unknown(
            owner_id,
            workspace_id,
            session_id,
        )

    @staticmethod
    def _restore_reserved_planning_task_for_retry(
        cur: Any, session: Mapping[str, Any]
    ) -> None:
        """Compatibility facade for the unified retry-authorized lifecycle fact."""
        ResearchStartRepository._apply_reserved_planning_task_lifecycle_fact(
            cur,
            session,
            fact=ResearchStartLifecycleFact.RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED,
        )

    def mark_external_result_unknown(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        attempt_id: str,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._recovery_operations().mark_external_result_unknown(
            request_id,
            worker_id,
            lease_epoch,
            attempt_id,
            result,
        )

    @staticmethod
    def _mark_reserved_planning_task(
        cur: Any,
        session: Mapping[str, Any],
        *,
        task_status: str,
        projection_status: str,
    ) -> None:
        """Compatibility facade for callers not yet expressed as lifecycle facts."""
        fact = {
            ("failed", "failed"): ResearchStartLifecycleFact.PLANNING_FAILED,
            ("awaiting_confirmation", "external_result_unknown"): (
                ResearchStartLifecycleFact.EXTERNAL_RESULT_UNKNOWN
            ),
        }.get((task_status, projection_status))
        if fact is None:
            raise ValueError("unsupported reserved planning Task lifecycle transition")
        ResearchStartRepository._apply_reserved_planning_task_lifecycle_fact(
            cur,
            session,
            fact=fact,
        )

    @staticmethod
    def _apply_reserved_planning_task_lifecycle_fact(
        cur: Any,
        session: Mapping[str, Any],
        *,
        fact: ResearchStartLifecycleFact,
    ) -> None:
        """Submit a Research Start fact to the sole Task lifecycle writer."""
        try:
            apply_research_start_lifecycle_fact_locked(
                cur,
                session=session,
                fact=fact,
            )
        except TaskLifecycleWriteConflictError as exc:
            raise TerminalStateError() from exc

    @staticmethod
    def _set_unknown(
        cur: Any,
        session: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        worker_id: str | None = None,
        lease_epoch: int | None = None,
        return_public: bool = True,
    ) -> dict[str, Any] | None:
        params: list[Any] = [
            request["request_id"],
            request["session_id"],
            request["owner_id"],
            request["workspace_id"],
        ]
        lease_sql = "AND lease_expires_at <= clock_timestamp()"
        if worker_id is not None and lease_epoch is not None:
            lease_sql = (
                "AND lease_owner = %s AND lease_epoch = %s "
                "AND lease_expires_at > clock_timestamp()"
            )
            params.extend([worker_id, lease_epoch])
        cur.execute(
            f"""UPDATE research_start_requests
                   SET state = 'unknown', lease_owner = NULL, lease_expires_at = NULL,
                       result_payload = NULL, updated_at = clock_timestamp()
                 WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
                   AND state = 'leased' {lease_sql}""",
            params,
        )
        if cur.rowcount != 1:
            raise TerminalStateError()
        ResearchStartRepository._apply_reserved_planning_task_lifecycle_fact(
            cur,
            session,
            fact=ResearchStartLifecycleFact.EXTERNAL_RESULT_UNKNOWN,
        )
        returning_sql = f" RETURNING {SESSION_COLUMNS}" if return_public else ""
        cur.execute(
            f"""UPDATE research_start_sessions
                  SET state = 'external_result_unknown', result_payload = NULL, user_message = %s,
                      failure_code = 'external_result_unknown', updated_at = clock_timestamp()
                  WHERE session_id = %s AND owner_id = %s AND workspace_id = %s AND state = 'running'
                  {returning_sql}""",
            (UNKNOWN_USER_MESSAGE, session["session_id"], session["owner_id"], session["workspace_id"]),
        )
        if cur.rowcount != 1:
            raise TerminalStateError()
        if not return_public:
            return None
        unknown = _row(cur.fetchone())
        if unknown is None:
            raise TerminalStateError()
        return _session_public(unknown)

    def reconcile_expired_leases(self) -> dict[str, int]:
        return self._recovery_operations().reconcile_expired_leases()

    def record_attempt_response(
        self, request_id: str, worker_id: str, lease_epoch: int, attempt_id: str,
        *, raw: bytes, metadata: Mapping[str, Any],
    ) -> None:
        """Durably capture an observed response before decode under the same lease.

        The response is neither a succeeded attempt nor an accepted ResearchDesign.
        Raw content is stored directly on the authoritative attempt row.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                session, request = self._lock_path(cur, request_id)
                self._require_live_lease(session, request, worker_id, lease_epoch)
                attempt = self._lock_attempt(cur, request_id, attempt_id)
                if attempt["state"] != "started" or attempt["fence_epoch"] != lease_epoch:
                    raise LeaseLostError()
                if metadata.get("phase") != attempt["phase"] or metadata.get("ordinal") != attempt["ordinal"]:
                    raise ValueError("response metadata does not match the fenced attempt")
                previous = _mapping(attempt.get("result_payload")) or {}
                existing = previous.get("responseEvidence")
                if isinstance(existing, Mapping):
                    saved_metadata = existing.get("metadata") or {}
                    if saved_metadata.get("responseSha256") == hashlib.sha256(raw).hexdigest() and all(
                        saved_metadata.get(key) == metadata.get(key)
                        for key in ("phase", "ordinal", "provider", "model", "requestSha256", "schemaSha256")
                    ):
                        return
                    raise ValueError("a different response is already recorded for this attempt")
                evidence = describe_response(
                    raw, scope={
                        "ownerId": str(session["owner_id"]),
                        "workspaceId": str(session["workspace_id"]),
                        "requestId": request_id, "attemptId": attempt_id,
                    }, metadata=metadata,
                )
                cur.execute(
                    """UPDATE research_start_attempts
                          SET result_payload = %s::jsonb, response_body = %s
                        WHERE attempt_id = %s AND request_id = %s
                          AND state = 'started' AND fence_epoch = %s
                          AND EXISTS (
                              SELECT 1 FROM research_start_requests r
                               WHERE r.request_id = %s AND r.state = 'leased'
                                 AND r.lease_owner = %s AND r.lease_epoch = %s
                                 AND r.lease_expires_at > clock_timestamp()
                          )""",
                    (_json({**dict(previous), "responseEvidence": evidence}), raw, attempt_id,
                     request_id, lease_epoch, request_id, worker_id, lease_epoch),
                )
                if cur.rowcount != 1:
                    raise LeaseLostError()

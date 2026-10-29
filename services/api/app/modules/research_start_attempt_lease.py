"""Fenced attempt and lease persistence for durable Research Start."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.modules.catalog_execution_binding import binding_from_session
from app.modules.research_start_errors import (
    AttemptAlreadyStartedError,
    AttemptNotFoundError,
    LeaseLostError,
    RequestNotFoundError,
    SessionNotFoundError,
    TerminalStateError,
)
from app.modules.research_start_projection import (
    REQUEST_TERMINAL_STATES,
    _attempt_internal,
    _claim_internal,
    _row,
)


ATTEMPT_PHASES = frozenset({"research_planner"})
SESSION_TERMINAL_STATES = frozenset({"completed", "failed", "external_result_unknown"})
SESSION_COLUMNS = """session_id, owner_id, workspace_id, state, reserved_task_id,
reserved_artifact_id, result_payload, user_message, failure_code, catalog_intent_id,
selected_resource_id, selected_resource_version, selected_capability_id, created_at, updated_at"""
REQUEST_COLUMNS = """request_id, session_id, owner_id, workspace_id, client_request_id,
fingerprint, state, lease_owner, lease_epoch, lease_expires_at, runtime_source_version,
request_payload, result_payload, created_at, updated_at"""
ATTEMPT_COLUMNS = """attempt_id, request_id, phase, ordinal, state, fence_epoch,
result_payload, response_body, started_at, finished_at"""


def require_nonempty(**values: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("durable research-start identifiers must be non-empty strings")


def locate_request(cur: Any, request_id: str) -> dict[str, Any]:
    cur.execute(
        """SELECT request_id, session_id, owner_id, workspace_id
             FROM research_start_requests WHERE request_id = %s""",
        (request_id,),
    )
    locator = _row(cur.fetchone())
    if locator is None:
        raise RequestNotFoundError()
    return locator


def lock_session(cur: Any, session_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
    cur.execute(
        f"""SELECT {SESSION_COLUMNS} FROM research_start_sessions
              WHERE session_id = %s AND owner_id = %s AND workspace_id = %s
              FOR UPDATE""",
        (session_id, owner_id, workspace_id),
    )
    session = _row(cur.fetchone())
    if session is None:
        raise SessionNotFoundError()
    return session


def lock_request(
    cur: Any, request_id: str, session_id: str, owner_id: str, workspace_id: str
) -> dict[str, Any]:
    cur.execute(
        f"""SELECT {REQUEST_COLUMNS},
                   (lease_expires_at > clock_timestamp()) AS lease_is_live
              FROM research_start_requests
             WHERE request_id = %s AND session_id = %s AND owner_id = %s AND workspace_id = %s
             FOR UPDATE""",
        (request_id, session_id, owner_id, workspace_id),
    )
    request = _row(cur.fetchone())
    if request is None:
        raise RequestNotFoundError()
    return request


def lock_attempt(cur: Any, request_id: str, attempt_id: str) -> dict[str, Any]:
    cur.execute(
        f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
              WHERE request_id = %s AND attempt_id = %s FOR UPDATE""",
        (request_id, attempt_id),
    )
    attempt = _row(cur.fetchone())
    if attempt is None:
        raise AttemptNotFoundError()
    return attempt


def lock_path(cur: Any, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    locator = locate_request(cur, request_id)
    session = lock_session(cur, locator["session_id"], locator["owner_id"], locator["workspace_id"])
    request = lock_request(
        cur, request_id, locator["session_id"], locator["owner_id"], locator["workspace_id"]
    )
    return session, request


def require_live_lease(
    session: Mapping[str, Any], request: Mapping[str, Any], worker_id: str, lease_epoch: int
) -> None:
    if session["state"] in SESSION_TERMINAL_STATES or request["state"] in REQUEST_TERMINAL_STATES:
        raise TerminalStateError()
    if (
        request["state"] != "leased"
        or request["lease_owner"] != worker_id
        or request["lease_epoch"] != lease_epoch
        or request["lease_is_live"] is not True
    ):
        raise LeaseLostError()


class ResearchStartAttemptLeaseRepository:
    """Perform attempt/lease transactions without owning session finalization."""

    def __init__(self, *, connect: Callable[[], Any], new_id: Callable[[str], str]) -> None:
        self._connect = connect
        self._new_id = new_id

    def claim(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        not_before: datetime | None = None,
    ) -> dict[str, Any] | None:
        require_nonempty(worker_id=worker_id)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not_before is not None:
            if not_before.tzinfo is None:
                raise ValueError("not_before must include a timezone")
            not_before = not_before.astimezone(UTC)
        session_cutoff_sql = " AND r.created_at >= %s" if not_before is not None else ""
        request_cutoff_sql = " AND created_at >= %s" if not_before is not None else ""
        cutoff_params: tuple[datetime, ...] = (not_before,) if not_before is not None else ()
        with self._connect() as conn:
            with conn.cursor() as cur:
                # PostgreSQL is the source/version fence authority. A pre-fence
                # worker does not set this transaction-local value, so the DB
                # trigger rejects every queued -> leased transition it attempts.
                # This worker additionally skips legacy/mismatched queued rows.
                cur.execute(
                    "SELECT set_config('openair.runtime_source_version', %s, true)",
                    (settings.runtime_source_version,),
                )
                cur.execute(
                    f"""SELECT {SESSION_COLUMNS} FROM research_start_sessions s
                          WHERE s.state = 'running' AND EXISTS (
                              SELECT 1 FROM research_start_requests r
                               WHERE r.session_id = s.session_id AND r.owner_id = s.owner_id
                                 AND r.workspace_id = s.workspace_id AND r.state = 'queued'
                                 AND r.runtime_source_version = %s{session_cutoff_sql}
                          )
                          ORDER BY s.created_at, s.session_id FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (settings.runtime_source_version, *cutoff_params),
                )
                session = _row(cur.fetchone())
                if session is None:
                    return None
                cur.execute(
                    f"""SELECT request_id FROM research_start_requests
                         WHERE session_id = %s AND owner_id = %s AND workspace_id = %s """
                    f"""AND state = 'queued'
                           AND runtime_source_version = %s{request_cutoff_sql}
                         ORDER BY created_at, request_id FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (
                        session["session_id"],
                        session["owner_id"],
                        session["workspace_id"],
                        settings.runtime_source_version,
                        *cutoff_params,
                    ),
                )
                candidate = _row(cur.fetchone())
                if candidate is None:
                    return None
                lock_request(
                    cur,
                    candidate["request_id"],
                    session["session_id"],
                    session["owner_id"],
                    session["workspace_id"],
                )
                cur.execute(
                    f"""UPDATE research_start_requests
                          SET state = 'leased', lease_owner = %s, lease_epoch = lease_epoch + 1,
                              lease_expires_at = clock_timestamp() + (%s * INTERVAL '1 second'),
                              updated_at = clock_timestamp()
                          WHERE request_id = %s AND session_id = %s AND owner_id = %s """
                    f"""AND workspace_id = %s
                            AND state = 'queued'
                          RETURNING {REQUEST_COLUMNS}""",
                    (
                        worker_id,
                        lease_seconds,
                        candidate["request_id"],
                        session["session_id"],
                        session["owner_id"],
                        session["workspace_id"],
                    ),
                )
                claimed = _row(cur.fetchone())
                if claimed is None:
                    raise LeaseLostError()
                return _claim_internal(claimed)

    def renew(
        self,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        lease_seconds: int = 60,
        query_timeout_ms: int = 2000,
    ) -> dict[str, Any]:
        require_nonempty(request_id=request_id, worker_id=worker_id)
        if lease_seconds <= 0 or query_timeout_ms <= 0:
            raise ValueError("lease and query timeouts must be positive")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{query_timeout_ms}ms",),
                )
                session, request = lock_path(cur, request_id)
                require_live_lease(session, request, worker_id, lease_epoch)
                cur.execute(
                    f"""UPDATE research_start_requests
                          SET lease_expires_at = clock_timestamp() + (%s * INTERVAL '1 second'),
                              updated_at = clock_timestamp()
                          WHERE request_id = %s AND session_id = %s AND owner_id = %s """
                    f"""AND workspace_id = %s
                            AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                            AND lease_expires_at > clock_timestamp()
                          RETURNING {REQUEST_COLUMNS}""",
                    (
                        lease_seconds,
                        request_id,
                        request["session_id"],
                        request["owner_id"],
                        request["workspace_id"],
                        worker_id,
                        lease_epoch,
                    ),
                )
                renewed = _row(cur.fetchone())
                if renewed is None:
                    raise LeaseLostError()
                return _claim_internal(renewed)

    def load_execution_context(
        self, request_id: str, worker_id: str, lease_epoch: int
    ) -> dict[str, Any]:
        """Load the smallest fenced worker DTO needed to resume a request."""
        require_nonempty(request_id=request_id, worker_id=worker_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                session, request = lock_path(cur, request_id)
                require_live_lease(session, request, worker_id, lease_epoch)
                cur.execute(
                    f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
                          WHERE request_id = %s ORDER BY phase, ordinal FOR UPDATE""",
                    (request_id,),
                )
                attempts = []
                for raw in cur.fetchall():
                    attempt = _row(raw) or {}
                    result = attempt.get("result_payload")
                    if isinstance(result, str):
                        result = json.loads(result)
                    attempts.append(
                        {
                            "attemptId": attempt["attempt_id"],
                            "phase": attempt["phase"],
                            "ordinal": attempt["ordinal"],
                            "status": attempt["state"],
                            "fenceEpoch": attempt["fence_epoch"],
                            "result": result,
                        }
                    )
                intent = request["request_payload"]
                if isinstance(intent, str):
                    intent = json.loads(intent)
                intent = dict(intent)
                created_by = str(intent.pop("_createdBy", request["owner_id"]))
                catalog_binding = binding_from_session(session)
                return {
                    "requestId": request["request_id"],
                    "sessionId": session["session_id"],
                    "ownerId": request["owner_id"],
                    "createdBy": created_by,
                    "workspaceId": request["workspace_id"],
                    "intent": intent,
                    "catalogBinding": (
                        catalog_binding.to_public() if catalog_binding is not None else None
                    ),
                    "reservedTaskId": session["reserved_task_id"],
                    "reservedArtifactId": session["reserved_artifact_id"],
                    "leaseEpoch": request["lease_epoch"],
                    "runtimeSourceVersion": request.get("runtime_source_version"),
                    "attempts": attempts,
                }

    def begin_attempt(
        self, request_id: str, worker_id: str, lease_epoch: int, phase: str, ordinal: int
    ) -> dict[str, Any]:
        require_nonempty(request_id=request_id, worker_id=worker_id)
        if phase not in ATTEMPT_PHASES or ordinal <= 0:
            raise ValueError("unsupported attempt phase or ordinal")
        with self._connect() as conn:
            with conn.cursor() as cur:
                session, request = lock_path(cur, request_id)
                require_live_lease(session, request, worker_id, lease_epoch)
                cur.execute(
                    f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
                          WHERE request_id = %s AND state = 'started'
                          ORDER BY started_at, attempt_id LIMIT 1 FOR UPDATE""",
                    (request_id,),
                )
                started = _row(cur.fetchone())
                if started is not None:
                    if (
                        started["phase"] == phase
                        and started["ordinal"] == ordinal
                        and started["fence_epoch"] == lease_epoch
                    ):
                        return _attempt_internal(started, permitted=False, replay=True)
                    raise AttemptAlreadyStartedError()
                cur.execute(
                    f"""SELECT {ATTEMPT_COLUMNS} FROM research_start_attempts
                          WHERE request_id = %s AND phase = %s """
                    f"""AND (ordinal = %s OR state = 'succeeded')
                          ORDER BY ordinal LIMIT 1 FOR UPDATE""",
                    (request_id, phase, ordinal),
                )
                if cur.fetchone() is not None:
                    raise AttemptAlreadyStartedError()

                attempt_id = self._new_id("rsa")
                cur.execute(
                    f"""INSERT INTO research_start_attempts(
                           attempt_id, request_id, phase, ordinal, fence_epoch
                       )
                       SELECT %s, %s, %s, %s, %s
                        WHERE EXISTS (
                            SELECT 1 FROM research_start_requests
                             WHERE request_id = %s AND session_id = %s AND owner_id = %s """
                    f"""AND workspace_id = %s
                               AND state = 'leased' AND lease_owner = %s AND lease_epoch = %s
                               AND lease_expires_at > clock_timestamp()
                        )
                       RETURNING {ATTEMPT_COLUMNS}""",
                    (
                        attempt_id,
                        request_id,
                        phase,
                        ordinal,
                        lease_epoch,
                        request_id,
                        request["session_id"],
                        request["owner_id"],
                        request["workspace_id"],
                        worker_id,
                        lease_epoch,
                    ),
                )
                attempt = _row(cur.fetchone())
                if attempt is None:
                    raise LeaseLostError()
                return _attempt_internal(attempt, permitted=True, replay=False)

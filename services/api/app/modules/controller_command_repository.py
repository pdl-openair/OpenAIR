from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.durable_execution import DurableExecutionError
from app.modules.research_progress_projection import stamp_progress_projection
from app.modules.task_message_repository import TaskMessageRepository


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else str(value)


_CANONICAL_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}
_LEGACY_TERMINAL_TASK_STATES = {"completed", "cancelled"}

# Durable executor identities and revision reasons, never display labels.
_SUPPLEMENT_EXCLUDED_EXECUTOR_IDS = frozenset({
    "openair_summary_agent",
    "result_reliability_audit",
})
_SUPPLEMENT_RECOVERY_REVISION_REASON_CODES = frozenset({
    "research_revision",
    "unknown_confirmed_never_started",
})


def canonical_progression_selection_request_id(command_id: str) -> str:
    normalized = str(command_id or "").strip()
    if not normalized:
        raise ValueError("controller command id must be non-empty")
    return f"controller-command:{normalized}:research-revision"


def canonical_progression_recovery_identity(command_id: str) -> str:
    normalized = str(command_id or "").strip()
    if not normalized:
        raise ValueError("controller command id must be non-empty")
    return f"controller-command:{normalized}"


def _safe_decision_sequence(value: Any) -> int | None:
    """Return a durable decisionSequence, or None when the value is not a real int."""
    return value if type(value) is int else None


def is_supplement_eligible_primary_work_unit(
    *,
    task_payload: dict[str, Any],
    work_unit: dict[str, Any],
    capability_call_work_unit_ids: set[str],
    controller_command_id: str | None = None,
) -> bool:
    """Fail closed unless durable canonical facts identify an untouched primary research unit."""
    task_id = str(task_payload.get("taskId") or "").strip()
    work_unit_id = str(work_unit.get("workUnitId") or "").strip()
    decision_id = str(work_unit.get("executionDecisionId") or "").strip()
    assignment_id = str(work_unit.get("assignmentId") or "").strip()
    if (
        not task_id
        or not work_unit_id
        or not decision_id
        or not assignment_id
        or str(work_unit.get("taskId") or "").strip() != task_id
        or work_unit.get("sourceMode") != "live"
        or work_unit.get("dispatchable") is not True
        or work_unit.get("status") != "queued"
        or work_unit_id in capability_call_work_unit_ids
    ):
        return False

    decisions = [
        row
        for row in task_payload.get("executionDecisions", [])
        if isinstance(row, dict)
        and str(row.get("executionDecisionId") or "").strip() == decision_id
    ]
    if len(decisions) != 1:
        return False
    decision = decisions[0]
    revision_reason = decision.get("revisionReasonCode")
    if (
        str(decision.get("taskId") or "").strip() != task_id
        or decision.get("status") != "dispatched"
        or _safe_decision_sequence(decision.get("decisionSequence")) is None
    ):
        return False
    if revision_reason in _SUPPLEMENT_RECOVERY_REVISION_REASON_CODES:
        if (
            controller_command_id is None
            or decision.get("selectionRequestId")
            != canonical_progression_selection_request_id(controller_command_id)
        ):
            return False

    assignments = [
        row
        for row in decision.get("assignments", [])
        if isinstance(row, dict)
        and str(row.get("assignmentId") or "").strip() == assignment_id
    ]
    if len(assignments) != 1:
        return False
    assignment = assignments[0]
    if assignment.get("role") != "primary":
        return False

    assembly_policy = decision.get("resultAssemblyPolicy")
    if not isinstance(assembly_policy, dict):
        return False
    if str(assembly_policy.get("assemblerAssignmentId") or "").strip() == assignment_id:
        return False

    executor_ref = assignment.get("executorRef")
    if not isinstance(executor_ref, dict) or executor_ref != work_unit.get("executorRef"):
        return False
    if str(executor_ref.get("executorId") or "").strip() in _SUPPLEMENT_EXCLUDED_EXECUTOR_IDS:
        return False
    return True


def _lifecycle_kind(task_payload: dict[str, Any]) -> str:
    canonical_keys = {"researchDesignVersions", "executionDecisions", "workUnits"}
    return "canonical_v1_2" if canonical_keys.issubset(task_payload) else "legacy_durable_run"


def _target_boundary(task_payload: dict[str, Any]) -> dict[str, Any]:
    work_unit_ids = sorted({
        str(row.get("workUnitId") or "").strip()
        for row in task_payload.get("workUnits", [])
        if isinstance(row, dict) and str(row.get("workUnitId") or "").strip()
    })
    decisions = [row for row in task_payload.get("executionDecisions", []) if isinstance(row, dict)]
    latest = max(
        decisions,
        key=lambda row: (int(row.get("decisionSequence") or 0), str(row.get("decidedAt") or "")),
        default=None,
    )
    return {
        "knownWorkUnitIds": work_unit_ids,
        "submittedAfterExecutionDecisionId": (
            (str(latest.get("executionDecisionId") or "").strip() or None)
            if latest is not None
            else None
        ),
    }


def _canonical_bound_work_unit_id(boundary: Any) -> str | None:
    """Return the received-state canonical binding, if materialization made one."""
    if not isinstance(boundary, dict):
        return None
    value = str(boundary.get("canonicalWorkUnitId") or "").strip()
    return value or None


class ControllerCommandRepository:
    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conn = psycopg.connect(
            f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_database} "
            f"user={settings.postgres_user}", password=settings.postgres_password, row_factory=dict_row,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('openair.execution_namespace', %s, true)",
                    (settings.execution_namespace,),
                )
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "commandId": row["command_id"], "taskId": row["task_id"], "kind": row["kind"],
            "messageId": f"msg_controller_command_{row['command_id']}",
            "state": row["state"],
            "userMessage": str((row.get("payload") or {}).get("text") or row["user_message"]),
            "submittedTaskRevision": int(row.get("submitted_task_revision") or 0),
            "appliedExecutionDecisionId": row.get("applied_execution_decision_id"),
            "appliedWorkUnitId": (
                row.get("applied_canonical_work_unit_id") or row.get("applied_work_unit_id")
            ),
            "terminalReasonCode": row.get("terminal_reason_code"),
            "submittedAt": _iso(row["created_at"]), "updatedAt": _iso(row["updated_at"]),
        }

    def create(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        kind: str,
        text: str,
        expected_task_revision: int,
        idempotency_key: str,
        canonical_progression_available: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise DurableExecutionError(428, "Idempotency-Key is required")
        if len(idempotency_key) > 128:
            raise DurableExecutionError(422, "Idempotency-Key must be at most 128 characters")
        if type(expected_task_revision) is not int or expected_task_revision < 0:
            raise DurableExecutionError(422, "expectedTaskRevision must be a non-negative integer")
        payload = {"text": text}
        fingerprint = hashlib.sha256(_json({
            "expectedTaskRevision": expected_task_revision,
            "kind": kind,
            "text": text,
        }).encode("utf-8")).hexdigest()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT task_id, status, revision, payload FROM openair_tasks
                       WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                         AND status <> 'deleted' FOR UPDATE""",
                    (task_id, owner_id, workspace_id),
                )
                task = cur.fetchone()
                if not task:
                    raise DurableExecutionError(404, "task not found")
                task_payload = deepcopy(task["payload"] or {})
                lifecycle_kind = _lifecycle_kind(task_payload)
                cur.execute(
                    """SELECT * FROM openair_controller_commands
                       WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                         AND idempotency_key=%s
                       FOR UPDATE""",
                    (task_id, owner_id, workspace_id, idempotency_key),
                )
                existing = cur.fetchone()
                if existing:
                    if existing["request_fingerprint"] != fingerprint:
                        raise DurableExecutionError(409, "idempotency_key_request_fingerprint_conflict")
                    if (
                        lifecycle_kind == "canonical_v1_2"
                        and existing.get("state") == "received"
                        and not canonical_progression_available
                    ):
                        terminal_reason = "supplement_progression_service_unavailable"
                        cur.execute(
                            """UPDATE openair_controller_commands
                                  SET state = 'rejected', terminal_reason_code = %s, updated_at = NOW()
                                WHERE command_id = %s AND state = 'received'""",
                            (terminal_reason, existing["command_id"]),
                        )
                        if cur.rowcount != 1:
                            raise RuntimeError("controller_command_rejection_race")
                        existing = {
                            **existing, "state": "rejected",
                            "terminal_reason_code": terminal_reason,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    return self._public(existing), True
                if int(task["revision"]) != expected_task_revision:
                    raise DurableExecutionError(409, "controller_command_task_revision_conflict")

                terminal_states = (
                    _CANONICAL_TERMINAL_TASK_STATES
                    if lifecycle_kind == "canonical_v1_2"
                    else _LEGACY_TERMINAL_TASK_STATES
                )
                if str(task["status"]) in terminal_states:
                    raise DurableExecutionError(409, "controller_command_task_terminal")
                if lifecycle_kind == "canonical_v1_2":
                    from app.modules.workbench_user_actions import project_supplement_input_action

                    action = project_supplement_input_action(
                        {**task_payload, "status": str(task["status"]), "revision": int(task["revision"])},
                        progression_available=canonical_progression_available,
                    )
                    if action["allowed"] is not True:
                        raise DurableExecutionError(409, "supplement_input_not_allowed")
                boundary = _target_boundary(task_payload)
                recovery_visible_message = text if lifecycle_kind == "legacy_durable_run" else ""
                cur.execute(
                    """INSERT INTO openair_controller_commands
                        (command_id, task_id, owner_id, workspace_id, kind, payload, state,
                         idempotency_key, request_fingerprint, user_message, submitted_task_revision,
                         lifecycle_kind, target_boundary)
                        VALUES (%s,%s,%s,%s,%s,%s::jsonb,'received',%s,%s,%s,%s,%s,%s::jsonb)
                        RETURNING *""",
                    (
                        f"controller_command_{uuid4().hex}", task_id, owner_id, workspace_id, kind,
                        _json(payload), idempotency_key, fingerprint, recovery_visible_message,
                        expected_task_revision,
                        lifecycle_kind, _json(boundary),
                    ),
                )
                created = cur.fetchone()
                if created is None:
                    raise RuntimeError("controller_command_insert_failed")
                if lifecycle_kind == "canonical_v1_2" and not canonical_progression_available:
                    terminal_reason = "supplement_progression_service_unavailable"
                    cur.execute(
                        """UPDATE openair_controller_commands
                              SET state = 'rejected', terminal_reason_code = %s, updated_at = NOW()
                            WHERE command_id = %s AND state = 'received'""",
                        (terminal_reason, created["command_id"]),
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError("controller_command_rejection_race")
                    created = {
                        **created,
                        "state": "rejected",
                        "terminal_reason_code": terminal_reason,
                        "updated_at": datetime.now(timezone.utc),
                    }
                if (
                    lifecycle_kind == "canonical_v1_2"
                    and str(task["status"]) == "running"
                    and created.get("state") == "received"
                ):
                    # A supplement that wins the Task lock before provider I/O
                    # invalidates only still-safe dispatch fences. If provider I/O
                    # already started, that call is left untouched and the command
                    # waits for the next safe execution boundary instead.
                    cur.execute(
                        """UPDATE openair_dispatch
                              SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                                  authorization_task_revision=NULL, submission_mode=NULL,
                                  next_attempt_at=clock_timestamp(), updated_at=clock_timestamp()
                            WHERE task_id=%s
                              AND status IN ('claimed', 'submission_authorized')
                              AND external_action_started_at IS NULL""",
                        (task_id,),
                    )
                message_id = f"msg_controller_command_{created['command_id']}"
                now = _iso(datetime.now(timezone.utc))
                TaskMessageRepository().append(
                    cur,
                    task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                    role="user", text=text, message_uid=message_id,
                    message_type="controller_supplement",
                    metadata={"controllerCommandId": created["command_id"]},
                )
                next_revision = expected_task_revision + 1
                task_payload["updatedAt"] = now
                task_payload["revision"] = next_revision
                stamp_progress_projection(task_payload, next_revision=next_revision)
                cur.execute(
                    """UPDATE openair_tasks
                       SET updated_at = NOW(), revision = %s, payload = %s::jsonb
                       WHERE task_id=%s AND owner_id=%s AND workspace_id=%s AND revision=%s
                         AND status <> 'deleted'""",
                    (next_revision, _json(task_payload), task_id, owner_id, workspace_id, expected_task_revision),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("task_revision_conflict")
                return self._public(created), False

    def reject_received(
        self,
        *,
        task_id: str,
        command_id: str,
        owner_id: str,
        workspace_id: str,
        reason_code: str,
    ) -> dict[str, Any] | None:
        """Durably terminalize a command when the progression trigger cannot finish it."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_controller_commands
                          SET state = 'rejected', terminal_reason_code = %s,
                              progression_lease_owner = NULL,
                              progression_lease_expires_at = NULL,
                              progression_last_error = %s,
                              updated_at = NOW()
                        WHERE command_id = %s AND task_id = %s
                          AND owner_id = %s AND workspace_id = %s AND state = 'received'
                        RETURNING *""",
                    (reason_code, reason_code, command_id, task_id, owner_id, workspace_id),
                )
                row = cur.fetchone()
                if row is not None:
                    return self._public(row)
                cur.execute(
                    """SELECT * FROM openair_controller_commands
                         WHERE command_id = %s AND task_id = %s
                           AND owner_id = %s AND workspace_id = %s""",
                    (command_id, task_id, owner_id, workspace_id),
                )
                existing = cur.fetchone()
                return self._public(existing) if existing else None

    def record_progression_failure(
        self,
        *,
        task_id: str,
        command_id: str,
        owner_id: str,
        workspace_id: str,
        error_code: str,
        retry_after_seconds: int = 15,
    ) -> dict[str, Any] | None:
        """Release the lease and retain a safe failure fact without terminal rejection."""
        normalized_error = error_code.strip()
        if (
            not normalized_error
            or len(normalized_error) > 160
            or not normalized_error.isascii()
            or normalized_error != normalized_error.lower()
            or not all(
                character.isalnum() or character in "_.:-"
                for character in normalized_error
            )
        ):
            raise ValueError("invalid controller command progression error code")
        if type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 3600:
            raise ValueError("invalid controller command progression retry delay")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_controller_commands
                          SET progression_lease_owner = NULL,
                              progression_lease_expires_at = NULL,
                              progression_last_error = %s,
                              progression_next_attempt_at = NOW() + (%s * INTERVAL '1 second'),
                              updated_at = NOW()
                        WHERE command_id = %s AND task_id = %s
                          AND owner_id = %s AND workspace_id = %s
                          AND lifecycle_kind = 'canonical_v1_2'
                          AND state = 'received'
                        RETURNING *""",
                    (
                        normalized_error,
                        retry_after_seconds,
                        command_id,
                        task_id,
                        owner_id,
                        workspace_id,
                    ),
                )
                row = cur.fetchone()
                if row is not None:
                    return self._public(row)
                cur.execute(
                    """SELECT * FROM openair_controller_commands
                         WHERE command_id = %s AND task_id = %s
                           AND owner_id = %s AND workspace_id = %s""",
                    (command_id, task_id, owner_id, workspace_id),
                )
                existing = cur.fetchone()
                return self._public(existing) if existing else None

    def claim_received_for_progression(
        self,
        *,
        task_id: str,
        command_id: str,
        owner_id: str,
        workspace_id: str,
        lease_owner: str,
        lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Lease one received canonical command for idempotent recovery."""
        if not lease_owner.strip() or lease_seconds < 1:
            raise ValueError("invalid controller command progression lease")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_controller_commands
                          SET progression_lease_owner = %s,
                              progression_lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                              progression_attempt_count = progression_attempt_count + 1,
                              progression_last_error = NULL,
                              updated_at = NOW()
                        WHERE command_id = %s AND task_id = %s
                          AND owner_id = %s AND workspace_id = %s
                          AND lifecycle_kind = 'canonical_v1_2'
                          AND state = 'received'
                          AND progression_next_attempt_at <= NOW()
                          AND (
                              progression_lease_owner IS NULL
                              OR progression_lease_expires_at IS NULL
                              OR progression_lease_expires_at <= NOW()
                          )
                        RETURNING *""",
                    (
                        lease_owner,
                        lease_seconds,
                        command_id,
                        task_id,
                        owner_id,
                        workspace_id,
                    ),
                )
                row = cur.fetchone()
                return self._public(row) if row else None

    def get_lifecycle_kind(
        self, *, task_id: str, command_id: str, owner_id: str, workspace_id: str
    ) -> str | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT lifecycle_kind FROM openair_controller_commands
                         WHERE command_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s""",
                    (command_id, task_id, owner_id, workspace_id),
                )
                row = cur.fetchone()
                return str(row["lifecycle_kind"]) if row else None

    def get(self, *, task_id: str, command_id: str, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM openair_controller_commands WHERE command_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s", (command_id, task_id, owner_id, workspace_id))
                row = cur.fetchone()
                return self._public(row) if row else None

    def get_progression_context(
        self,
        *,
        task_id: str,
        command_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        """Return scoped durable facts needed to materialize one canonical supplement."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT c.command_id, c.task_id, c.kind, c.state, c.submitted_task_revision,
                              c.lifecycle_kind, c.target_boundary, c.progression_lease_owner,
                              c.progression_lease_expires_at, t.status AS task_status
                         FROM openair_controller_commands c
                         JOIN openair_tasks t
                           ON t.task_id=c.task_id AND t.owner_id=c.owner_id AND t.workspace_id=c.workspace_id
                        WHERE c.command_id=%s AND c.task_id=%s AND c.owner_id=%s AND c.workspace_id=%s
                          AND t.status <> 'deleted'""",
                    (command_id, task_id, owner_id, workspace_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return {
                    "commandId": row["command_id"],
                    "taskId": row["task_id"],
                    "kind": row["kind"],
                    "state": row["state"],
                    "submittedTaskRevision": int(row["submitted_task_revision"]),
                    "lifecycleKind": row["lifecycle_kind"],
                    "targetBoundary": deepcopy(row.get("target_boundary") or {}),
                    "progressionLeaseOwner": row.get("progression_lease_owner"),
                    "progressionLeaseExpiresAt": row.get("progression_lease_expires_at"),
                    "taskStatus": str(row.get("task_status") or ""),
                }

    def get_by_idempotency_key(
        self,
        *,
        task_id: str,
        idempotency_key: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        """Discover a committed command after an unknown POST response.

        This is a read-only lookup over the same scoped uniqueness key used by
        create().  It never creates a new key or replays the command.
        """
        normalized = idempotency_key.strip()
        if not normalized:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT * FROM openair_controller_commands
                       WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                         AND idempotency_key=%s""",
                    (task_id, owner_id, workspace_id, normalized),
                )
                row = cur.fetchone()
                return self._public(row) if row else None

    @staticmethod
    def apply_canonical_at_materialization(
        cursor: Any,
        *,
        task_payload: dict[str, Any],
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        """Bind canonical supplements to the next eligible primary research WorkUnit."""
        payload = deepcopy(task_payload)
        task_id = str(payload.get("taskId") or "").strip()
        if not task_id or _lifecycle_kind(payload) != "canonical_v1_2":
            return payload
        if str(payload.get("status") or "") in _CANONICAL_TERMINAL_TASK_STATES:
            ControllerCommandRepository.reject_canonical_for_terminal_task(
                cursor, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
            )
            return payload

        cursor.execute(
            """SELECT * FROM openair_controller_commands
               WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                 AND lifecycle_kind='canonical_v1_2' AND state='received'
               ORDER BY submitted_task_revision, created_at, command_id
               FOR UPDATE""",
            (task_id, owner_id, workspace_id),
        )
        commands = list(cursor.fetchall())
        if not commands:
            return payload

        payload_revision = payload.get("revision")
        work_units = [row for row in payload.get("workUnits", []) if isinstance(row, dict)]
        call_work_unit_ids = {
            str(row.get("workUnitId") or "").strip()
            for row in payload.get("capabilityCalls", [])
            if isinstance(row, dict) and str(row.get("workUnitId") or "").strip()
        }
        decision_sequences: dict[str, int] = {}
        for row in payload.get("executionDecisions", []):
            if not isinstance(row, dict):
                continue
            decision_id = str(row.get("executionDecisionId") or "").strip()
            sequence = _safe_decision_sequence(row.get("decisionSequence"))
            if not decision_id or sequence is None:
                continue
            decision_sequences[decision_id] = sequence
        supplements_by_work_unit: dict[str, list[tuple[dict[str, Any], str]]] = {}
        for command in commands:
            submitted_revision = command.get("submitted_task_revision")
            if (
                type(payload_revision) is not int
                or type(submitted_revision) is not int
                or payload_revision <= submitted_revision
            ):
                continue
            boundary = command.get("target_boundary")
            if not isinstance(boundary, dict):
                continue
            known_values = boundary.get("knownWorkUnitIds")
            if not isinstance(known_values, list):
                continue
            known_ids = {str(value).strip() for value in known_values if str(value).strip()}
            bound_work_unit_id = _canonical_bound_work_unit_id(boundary)
            if bound_work_unit_id:
                target = next(
                    (row for row in work_units if str(row.get("workUnitId") or "").strip() == bound_work_unit_id),
                    None,
                )
                if target is None:
                    continue
            else:
                eligible = [
                    row
                    for row in work_units
                    if str(row.get("workUnitId") or "").strip() not in known_ids
                    and str(row.get("executionDecisionId") or "").strip() in decision_sequences
                    and is_supplement_eligible_primary_work_unit(
                        task_payload=payload,
                        work_unit=row,
                        capability_call_work_unit_ids=call_work_unit_ids,
                        controller_command_id=str(command["command_id"]),
                    )
                ]
                eligible.sort(
                    key=lambda row: (
                        decision_sequences[str(row.get("executionDecisionId") or "").strip()],
                        str(row.get("dispatchedAt") or ""),
                        str(row.get("workUnitId") or ""),
                    )
                )
                if not eligible:
                    continue
                target = eligible[0]
                bound_work_unit_id = str(target.get("workUnitId") or "").strip()
                cursor.execute(
                    """UPDATE openair_controller_commands
                          SET target_boundary=target_boundary || %s::jsonb, updated_at=NOW()
                        WHERE command_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s
                          AND state='received'
                          AND NOT (target_boundary ? 'canonicalWorkUnitId')""",
                    (
                        json.dumps({
                            "canonicalWorkUnitId": bound_work_unit_id,
                            "canonicalExecutionDecisionId": str(target.get("executionDecisionId") or "").strip(),
                        }),
                        command["command_id"], task_id, owner_id, workspace_id,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                boundary["canonicalWorkUnitId"] = bound_work_unit_id
                boundary["canonicalExecutionDecisionId"] = str(target.get("executionDecisionId") or "").strip()
            target_id = str(target.get("workUnitId") or "").strip()
            text = str((command.get("payload") or {}).get("text") or command["user_message"]).strip()
            if text:
                supplements_by_work_unit.setdefault(target_id, []).append((command, text))

        for work_unit in work_units:
            target_id = str(work_unit.get("workUnitId") or "")
            supplements = supplements_by_work_unit.get(target_id)
            if not supplements:
                continue
            instruction = str(work_unit.get("instruction") or "").rstrip()
            additions = [
                (command, text)
                for command, text in supplements
                if f"[controllerCommandId={command['command_id']}]" not in instruction
            ]
            if additions:
                work_unit["instruction"] = instruction + "\n\n" + "用户在本工作单元冻结前补充的输入：\n" + "\n".join(
                    f"- [controllerCommandId={command['command_id']}] {text}"
                    for command, text in additions
                )
            # Do not transition received -> applied at materialization. A WorkUnit
            # can still lose submission authority after it is materialized. The
            # command becomes applied only when the provider submission carrying
            # this instruction is durably authorized.
        payload["workUnits"] = work_units
        return payload

    @staticmethod
    def _append_canonical_supplements(
        work_unit_payload: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        work_unit = deepcopy(work_unit_payload)
        instruction = str(work_unit.get("instruction") or "").rstrip()
        additions: list[str] = []
        for row in rows:
            command_id = str(row.get("command_id") or "").strip()
            text = str((row.get("payload") or {}).get("text") or row.get("user_message") or "").strip()
            if not command_id or not text:
                continue
            marker = f"[controllerCommandId={command_id}]"
            if marker in instruction:
                continue
            additions.append(f"- {marker} {text}")
        if additions:
            instruction += "\n\n用户在下一安全执行边界补充的要求：\n" + "\n".join(additions)
        work_unit["instruction"] = instruction
        return work_unit

    @staticmethod
    def prepare_canonical_at_authorization(
        cursor: Any,
        *,
        task_payload: dict[str, Any],
        work_unit_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> tuple[dict[str, Any], list[str]]:
        """Freeze received supplements into the next real provider payload."""
        payload = deepcopy(task_payload)
        task_id = str(payload.get("taskId") or "").strip()
        if (
            not task_id
            or _lifecycle_kind(payload) != "canonical_v1_2"
            or str(payload.get("status") or "") != "running"
        ):
            return {}, []
        work_units = [row for row in payload.get("workUnits", []) if isinstance(row, dict)]
        target = next(
            (row for row in work_units if str(row.get("workUnitId") or "").strip() == work_unit_id),
            None,
        )
        if target is None:
            return {}, []
        call_work_unit_ids = {
            str(row.get("workUnitId") or "").strip()
            for row in payload.get("capabilityCalls", [])
            if isinstance(row, dict) and str(row.get("workUnitId") or "").strip()
        }
        cursor.execute(
            """SELECT * FROM openair_controller_commands
                 WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                   AND lifecycle_kind='canonical_v1_2' AND kind='supplement_input'
                   AND state='received'
                 ORDER BY submitted_task_revision, created_at, command_id
                 FOR UPDATE""",
            (task_id, owner_id, workspace_id),
        )
        rows = list(cursor.fetchall())
        if not rows:
            return deepcopy(target), []
        rows = [
            row for row in rows
            if _canonical_bound_work_unit_id(row.get("target_boundary")) == work_unit_id
            and is_supplement_eligible_primary_work_unit(
                task_payload=payload,
                work_unit=target,
                capability_call_work_unit_ids=call_work_unit_ids,
                controller_command_id=str(row.get("command_id") or ""),
            )
        ]
        if not rows:
            return deepcopy(target), []
        command_ids = [str(row["command_id"]) for row in rows]
        return ControllerCommandRepository._append_canonical_supplements(target, rows), command_ids

    @staticmethod
    def mark_canonical_applied_at_action_start(
        cursor: Any,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        execution_decision_id: str,
        work_unit_id: str,
        command_ids: list[str],
    ) -> None:
        if not command_ids:
            return
        cursor.execute(
            """UPDATE openair_controller_commands
                  SET state='applied', target_execution_decision_id=%s,
                      applied_execution_decision_id=%s, applied_canonical_work_unit_id=%s,
                      terminal_reason_code=NULL,
                      progression_lease_owner=NULL, progression_lease_expires_at=NULL,
                      progression_last_error=NULL, updated_at=NOW()
                WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                  AND lifecycle_kind='canonical_v1_2' AND kind='supplement_input'
                  AND state='received' AND command_id = ANY(%s)
                  AND target_boundary->>'canonicalWorkUnitId'=%s
                  AND target_boundary->>'canonicalExecutionDecisionId'=%s""",
            (
                execution_decision_id,
                execution_decision_id,
                work_unit_id,
                task_id,
                owner_id,
                workspace_id,
                command_ids,
                work_unit_id,
                execution_decision_id,
            ),
        )
        if cursor.rowcount != len(command_ids):
            raise RuntimeError("canonical_controller_command_apply_conflict")

    @staticmethod
    def command_ids_in_execution_payload(work_unit_payload: dict[str, Any]) -> list[str]:
        """Read only command identities already frozen into one execution payload."""
        instruction = str(work_unit_payload.get("instruction") or "")
        command_ids: list[str] = []
        marker = "[controllerCommandId="
        for fragment in instruction.split(marker)[1:]:
            command_id = fragment.split("]", 1)[0].strip()
            if command_id and command_id not in command_ids:
                command_ids.append(command_id)
        return command_ids

    @staticmethod
    def restore_canonical_execution_context(
        cursor: Any,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        work_unit_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Rebuild applied supplement context for authorized recovery."""
        work_unit_id = str(work_unit_payload.get("workUnitId") or "").strip()
        if not work_unit_id:
            return deepcopy(work_unit_payload)
        cursor.execute(
            """SELECT * FROM openair_controller_commands
                 WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                   AND lifecycle_kind='canonical_v1_2' AND kind='supplement_input'
                   AND state='applied' AND applied_canonical_work_unit_id=%s
                 ORDER BY submitted_task_revision, created_at, command_id""",
            (task_id, owner_id, workspace_id, work_unit_id),
        )
        return ControllerCommandRepository._append_canonical_supplements(
            work_unit_payload,
            list(cursor.fetchall()),
        )

    @staticmethod
    def reject_canonical_for_terminal_task(
        cursor: Any,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> None:
        cursor.execute(
            """UPDATE openair_controller_commands
                  SET state='rejected', terminal_reason_code='task_terminal_before_application',
                      progression_lease_owner=NULL, progression_lease_expires_at=NULL,
                      progression_last_error='task_terminal_before_application',
                      updated_at=NOW()
                WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                  AND lifecycle_kind='canonical_v1_2' AND state='received'""",
            (task_id, owner_id, workspace_id),
        )

    def consume_at_work_unit_boundary(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        run_id: str,
        work_unit_id: str,
        worker_id: str,
        fence_epoch: int,
    ) -> list[str]:
        """Claim a boundary's received input, while recovering its prior applied input."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT r.run_id
                       FROM openair_execution_runs r
                       JOIN openair_execution_work_units wu ON wu.work_unit_id = %s
                       WHERE r.run_id = %s AND r.task_id = %s AND r.owner_id = %s AND r.workspace_id = %s
                         AND r.state = 'leased' AND r.lease_owner = %s AND r.lease_epoch = %s
                         AND r.lease_expires_at > NOW()
                         AND wu.run_id = r.run_id AND wu.task_id = r.task_id
                         AND wu.owner_id = r.owner_id AND wu.workspace_id = r.workspace_id
                         AND wu.state = 'running'
                       FOR UPDATE OF r, wu""",
                    (work_unit_id, run_id, task_id, owner_id, workspace_id, worker_id, fence_epoch),
                )
                if not cur.fetchone():
                    raise DurableExecutionError(409, "controller_command_boundary_not_claimable")
                cur.execute("""SELECT * FROM openair_controller_commands WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                               AND lifecycle_kind='legacy_durable_run'
                               AND (state='received' OR (state='applied' AND applied_run_id=%s AND applied_work_unit_id=%s))
                               ORDER BY created_at FOR UPDATE""", (task_id, owner_id, workspace_id, run_id, work_unit_id))
                rows = cur.fetchall()
                received = [row["command_id"] for row in rows if row["state"] == "received"]
                cur.execute("SELECT 1 FROM openair_execution_capability_calls WHERE work_unit_id=%s LIMIT 1", (work_unit_id,))
                call_exists = bool(cur.fetchone())
                if received and not call_exists:
                    cur.execute("""UPDATE openair_controller_commands SET state='applied', applied_run_id=%s,
                                   applied_work_unit_id=%s, updated_at=NOW() WHERE command_id = ANY(%s) AND state='received'""",
                                (run_id, work_unit_id, received))
                elif call_exists:
                    rows = [row for row in rows if row["state"] == "applied"]
                return [str((row["payload"] or {}).get("text") or row["user_message"]) for row in rows]

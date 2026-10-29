"""PostgreSQL authority for task recovery requests and successors."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.research_task_lifecycle import RecoveryLifecycleFact
from app.modules.research_task_lifecycle_repository import (
    TaskLifecycleWriteConflictError,
    commit_task_lifecycle_mutation_locked,
)


RECOVERABLE_STATES = {"failed", "blocked", "abandoned", "unknown"}


class RecoveryError(RuntimeError):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class RecoveryRepository:
    """Owns one transaction for each Recovery mutation.

    The lock order intentionally matches DurableExecutionRepository: connection,
    advisory task lock, Task row lock, then Run/request rows.
    """

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        try:
            conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)
        except psycopg.Error as exc:
            raise RecoveryError(503, "recovery_requires_postgres") from exc
        try:
            yield conn
            conn.commit()
        except psycopg.OperationalError as exc:
            conn.rollback()
            raise RecoveryError(503, "recovery_requires_postgres") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _lock_task(cur: Any, task_id: str) -> None:
        DurableExecutionRepository._lock_task(cur, task_id)

    @staticmethod
    def _lock_scoped_task(cur: Any, task_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        cur.execute(
            """SELECT * FROM openair_tasks
               WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                 AND status <> 'deleted' FOR UPDATE""",
            (task_id, owner_id, workspace_id),
        )
        task = cur.fetchone()
        if not task:
            raise RecoveryError(404, "recovery_not_found")
        return task

    @staticmethod
    def _locked_source(
        cur: Any, *, task_id: str, source_run_id: str, owner_id: str, workspace_id: str,
    ) -> dict[str, Any]:
        cur.execute(
            """SELECT * FROM openair_execution_runs
               WHERE run_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s FOR UPDATE""",
            (source_run_id, task_id, owner_id, workspace_id),
        )
        source = cur.fetchone()
        if not source:
            raise RecoveryError(404, "recovery_not_found")
        if source["state"] not in RECOVERABLE_STATES:
            raise RecoveryError(409, "recovery_preflight_failed")
        return source

    @staticmethod
    def _preserved_facts(cur: Any, source: dict[str, Any]) -> dict[str, Any]:
        cur.execute(
            """SELECT COUNT(*) AS artifact_count FROM openair_artifacts
               WHERE source_execution_run_id=%s AND owner_id=%s AND workspace_id=%s
                 AND source_task_id=%s""",
            (source["run_id"], source["owner_id"], source["workspace_id"], source["task_id"]),
        )
        row = cur.fetchone()
        return {
            "sourceRunState": str(source["state"]),
            "sourceRunNumber": int(source["run_number"]),
            "preservedArtifactCount": int(row["artifact_count"]),
        }

    def _response(
        self, cur: Any, *, request: dict[str, Any], task: dict[str, Any], source: dict[str, Any], replayed: bool,
    ) -> dict[str, Any]:
        return {
            "recoveryRequestId": request["request_id"],
            "taskId": request["task_id"],
            "sourceRunId": request["source_run_id"],
            "recoveryKind": request["recovery_kind"],
            "state": request["state"],
            "expectedTaskRevision": int(request["expected_task_revision"]),
            "taskRevision": int(task["revision"]),
            "successorRunId": request["successor_run_id"],
            "replayed": replayed,
            "allowedActions": [],
            "preservedFacts": self._preserved_facts(cur, source),
        }

    @staticmethod
    def _validate_message_commands(
        cur: Any, *, task_id: str, owner_id: str, workspace_id: str, message_ids: list[str],
    ) -> list[dict[str, Any]]:
        cur.execute(
            """SELECT command_id, user_message FROM openair_controller_commands
               WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                 AND kind='supplement_input' AND command_id = ANY(%s)
               FOR UPDATE""",
            (task_id, owner_id, workspace_id, message_ids),
        )
        commands = {str(row["command_id"]): row for row in cur.fetchall()}
        if len(commands) != len(message_ids):
            raise RecoveryError(409, "recovery_preflight_failed")
        ordered = [commands[message_id] for message_id in message_ids]
        if any(not str(row.get("user_message") or "").strip() for row in ordered):
            raise RecoveryError(409, "recovery_preflight_failed")
        return ordered

    @staticmethod
    def _validate_linked_messages(cur: Any, request: dict[str, Any]) -> list[dict[str, str]]:
        cur.execute(
            """SELECT rm.message_id, m.content FROM openair_recovery_request_messages rm
               JOIN openair_recovery_messages m
                 ON m.message_id=rm.message_id AND m.task_id=rm.task_id
                AND m.owner_id=rm.owner_id AND m.workspace_id=rm.workspace_id
               WHERE rm.request_id=%s AND rm.task_id=%s AND rm.owner_id=%s AND rm.workspace_id=%s
               ORDER BY rm.ordinal FOR UPDATE OF rm, m""",
            (request["request_id"], request["task_id"], request["owner_id"], request["workspace_id"]),
        )
        rows = cur.fetchall()
        if not rows or any(not str(row.get("content") or "").strip() for row in rows):
            raise RecoveryError(409, "recovery_preflight_failed")
        return [
            {"messageId": str(row["message_id"]), "content": str(row["content"]).strip()}
            for row in rows
        ]

    def _create_successor(
        self, cur: Any, *, task: dict[str, Any], source: dict[str, Any], request: dict[str, Any],
    ) -> dict[str, Any]:
        """Confirm a requested Recovery and make its sole successor atomically."""
        if int(task["revision"]) != int(request["expected_task_revision"]):
            raise RecoveryError(409, "recovery_stale")
        # Re-read under the task lock so delayed worker writes cannot become input.
        source = self._locked_source(
            cur,
            task_id=request["task_id"],
            source_run_id=request["source_run_id"],
            owner_id=request["owner_id"],
            workspace_id=request["workspace_id"],
        )
        recovery_messages = self._validate_linked_messages(cur, request)
        message_refs = [row["messageId"] for row in recovery_messages]
        cur.execute(
            "SELECT COALESCE(MAX(run_number), 0) AS max_run_number FROM openair_execution_runs WHERE task_id=%s",
            (request["task_id"],),
        )
        successor_number = int(cur.fetchone()["max_run_number"]) + 1
        successor_id = f"run_{uuid4().hex}"
        call_id = f"call_{uuid4().hex}"
        task_revision = int(task["revision"]) + 1

        payload = task["payload"] if isinstance(task["payload"], dict) else json.loads(task["payload"])
        payload = json.loads(_json(payload))
        payload["updatedAt"] = datetime.now().astimezone().isoformat().replace("+00:00", "Z")
        calls = list(payload.get("capabilityCalls") or [])
        calls.append({
            "id": call_id,
            "ownerId": request["owner_id"],
            "workspaceId": request["workspace_id"],
            "createdBy": request["owner_id"],
            "taskId": request["task_id"],
            "runId": successor_id,
            "name": "durable_recovery_execution",
            "status": "not_started",
            "requestedAt": payload["updatedAt"],
            "sourceMode": source["source_mode"],
            "runMode": "recovery",
        })
        payload["capabilityCalls"] = calls
        payload["executionRecovery"] = {
            "requestId": request["request_id"], "sourceRunId": source["run_id"], "state": "confirmed",
        }
        try:
            changed = commit_task_lifecycle_mutation_locked(
                cur,
                task_row=task,
                payload=payload,
                task_id=str(request["task_id"]),
                owner_id=str(request["owner_id"]),
                workspace_id=str(request["workspace_id"]),
                fact=RecoveryLifecycleFact.SUCCESSOR_EXECUTION_CONFIRMED,
            )
        except TaskLifecycleWriteConflictError as exc:
            raise RecoveryError(409, "recovery_stale") from exc
        if not changed:
            raise RecoveryError(409, "recovery_stale")
        task["revision"] = task_revision
        task["status"] = payload["status"]
        task["payload"] = payload

        source_snapshot = source["input_snapshot"]
        if not isinstance(source_snapshot, dict):
            source_snapshot = json.loads(source_snapshot)
        snapshot = json.loads(_json(source_snapshot))
        snapshot["taskId"] = request["task_id"]
        snapshot["controllerInputContext"] = [
            *list(snapshot.get("controllerInputContext") or []),
            *(row["content"] for row in recovery_messages),
        ]
        snapshot["recovery"] = {
            "requestId": request["request_id"],
            "sourceRunId": source["run_id"],
            "sourceRunNumber": int(source["run_number"]),
            "expectedTaskRevision": int(request["expected_task_revision"]),
            "recoveryKind": request["recovery_kind"],
            "recoveryMessageIds": message_refs,
        }
        cur.execute(
            """INSERT INTO openair_execution_runs
               (run_id, task_id, owner_id, workspace_id, task_revision, run_number, run_kind,
                idempotency_key, request_fingerprint, input_snapshot, target_substage, source_mode,
                capability_call_id, state)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,'queued') RETURNING *""",
            (
                successor_id, request["task_id"], request["owner_id"], request["workspace_id"], task_revision,
                successor_number, f"recovery_{request['recovery_kind']}", request["request_id"],
                request["payload_fingerprint"], _json(snapshot), source["target_substage"], source["source_mode"], call_id,
            ),
        )
        successor = cur.fetchone()
        cur.execute(
            """INSERT INTO openair_execution_capability_calls(call_id, run_id, task_id, state, source_mode)
               VALUES (%s,%s,%s,'queued',%s)""",
            (call_id, successor_id, request["task_id"], source["source_mode"]),
        )
        self._event(cur, successor, "start_requested", {
            "taskRevision": task_revision, "sourceMode": source["source_mode"], "recoveryRequestId": request["request_id"],
        })
        cur.execute(
            """UPDATE openair_task_recovery_requests
               SET state='confirmed', successor_run_id=%s, confirmed_at=clock_timestamp(), updated_at=clock_timestamp()
               WHERE request_id=%s AND state='requested' RETURNING *""",
            (successor_id, request["request_id"]),
        )
        confirmed = cur.fetchone()
        if not confirmed:
            raise RecoveryError(409, "recovery_successor_exists")
        cur.execute(
            """INSERT INTO openair_recovery_execution_projections
               (projection_id, request_id, task_id, source_run_id, successor_run_id, expected_task_revision,
                owner_id, workspace_id, recovery_kind, source_lease_epoch_fence, source_next_event_sequence_fence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                f"recovery_projection_{uuid4().hex}", confirmed["request_id"], confirmed["task_id"],
                confirmed["source_run_id"], successor_id, confirmed["expected_task_revision"], confirmed["owner_id"],
                confirmed["workspace_id"], confirmed["recovery_kind"], source["lease_epoch"], source["next_event_sequence"],
            ),
        )
        return confirmed

    @staticmethod
    def _event(cur: Any, run: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
        sequence = int(run["next_event_sequence"])
        cur.execute(
            "INSERT INTO openair_execution_run_events(run_id, sequence, type, payload) VALUES (%s,%s,%s,%s::jsonb)",
            (run["run_id"], sequence, event_type, _json(payload)),
        )
        cur.execute("UPDATE openair_execution_runs SET next_event_sequence=next_event_sequence+1 WHERE run_id=%s", (run["run_id"],))
        run["next_event_sequence"] = sequence + 1

    def create(self, *, task_id: str, owner_id: str, workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
        del task_id, owner_id, workspace_id, body
        raise RecoveryError(410, "legacy_recovery_read_only")

    def _retired_create(self, *, task_id: str, owner_id: str, workspace_id: str, body: dict[str, Any]) -> dict[str, Any]:
        del task_id, owner_id, workspace_id, body
        raise RecoveryError(410, "legacy_recovery_read_only")

        plan_fingerprint = body.get("planFingerprint") or _sha256({"planFingerprint": None})
        fingerprint_body = {
            "sourceRunId": body["sourceRunId"], "recoveryKind": body["recoveryKind"],
            "expectedTaskRevision": body["expectedTaskRevision"], "messageIds": body["messageIds"],
            "planFingerprint": plan_fingerprint,
        }
        payload_fingerprint = _sha256(fingerprint_body)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    self._lock_task(cur, task_id)
                    task = self._lock_scoped_task(cur, task_id, owner_id, workspace_id)
                    cur.execute(
                        """SELECT * FROM openair_task_recovery_requests
                           WHERE created_by=%s AND task_id=%s AND idempotency_key=%s FOR UPDATE""",
                        (owner_id, task_id, body["idempotencyKey"]),
                    )
                    existing = cur.fetchone()
                    if existing:
                        if existing["payload_fingerprint"] != payload_fingerprint:
                            raise RecoveryError(409, "idempotency_payload_conflict")
                        source = self._locked_source(
                            cur, task_id=task_id, source_run_id=existing["source_run_id"], owner_id=owner_id, workspace_id=workspace_id,
                        )
                        return self._response(cur, request=existing, task=task, source=source, replayed=True)
                    if int(task["revision"]) != int(body["expectedTaskRevision"]):
                        raise RecoveryError(409, "recovery_stale")
                    source = self._locked_source(
                        cur, task_id=task_id, source_run_id=body["sourceRunId"], owner_id=owner_id, workspace_id=workspace_id,
                    )
                    commands = self._validate_message_commands(
                        cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id, message_ids=body["messageIds"],
                    )
                    cur.execute(
                        """SELECT * FROM openair_task_recovery_requests
                           WHERE source_run_id=%s AND expected_task_revision=%s FOR UPDATE""",
                        (source["run_id"], body["expectedTaskRevision"]),
                    )
                    prior = cur.fetchone()
                    if prior:
                        if prior["state"] == "requested":
                            raise RecoveryError(409, "recovery_mutex_conflict")
                        if prior["state"] == "confirmed":
                            raise RecoveryError(409, "recovery_successor_exists")
                        raise RecoveryError(409, "recovery_terminal")
                    request_id = f"recovery_{uuid4().hex}"
                    cur.execute(
                        """INSERT INTO openair_task_recovery_requests
                           (request_id, task_id, source_run_id, recovery_kind, expected_task_revision, owner_id,
                            workspace_id, created_by, idempotency_key, payload_fingerprint, plan_fingerprint)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (
                            request_id, task_id, source["run_id"], body["recoveryKind"], body["expectedTaskRevision"], owner_id,
                            workspace_id, owner_id, body["idempotencyKey"], payload_fingerprint, plan_fingerprint,
                        ),
                    )
                    request = cur.fetchone()
                    for ordinal, command in enumerate(commands, start=1):
                        recovery_message_id = f"recovery_message_{uuid4().hex}"
                        content = str(command["user_message"]).strip()
                        cur.execute(
                            """INSERT INTO openair_recovery_messages
                               (message_id, task_id, owner_id, workspace_id, created_by, message_kind, content, payload_fingerprint)
                               VALUES (%s,%s,%s,%s,%s,'recovery_instruction',%s,%s)""",
                            (recovery_message_id, task_id, owner_id, workspace_id, owner_id, content, _sha256({"commandId": command["command_id"], "content": content})),
                        )
                        cur.execute(
                            """INSERT INTO openair_recovery_request_messages
                               (request_id, message_id, task_id, owner_id, workspace_id, ordinal)
                               VALUES (%s,%s,%s,%s,%s,%s)""",
                            (request_id, recovery_message_id, task_id, owner_id, workspace_id, ordinal),
                        )
                    if request["recovery_kind"] == "retry":
                        request = self._create_successor(cur, task=task, source=source, request=request)
                    return self._response(cur, request=request, task=task, source=source, replayed=False)
        except psycopg.errors.UniqueViolation as exc:
            constraint = getattr(exc.diag, "constraint_name", "") or ""
            if constraint == "uq_openair_task_recovery_requested_mutex" or "requested_mutex" in str(exc):
                raise RecoveryError(409, "recovery_mutex_conflict") from exc
            raise RecoveryError(409, "recovery_successor_exists") from exc
        except psycopg.Error as exc:
            raise RecoveryError(409, "recovery_preflight_failed") from exc

    def confirm(self, *, task_id: str, request_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        del task_id, request_id, owner_id, workspace_id
        raise RecoveryError(410, "legacy_recovery_read_only")

    def _retired_confirm(self, *, task_id: str, request_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        del task_id, request_id, owner_id, workspace_id
        raise RecoveryError(410, "legacy_recovery_read_only")

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    self._lock_task(cur, task_id)
                    task = self._lock_scoped_task(cur, task_id, owner_id, workspace_id)
                    cur.execute(
                        """SELECT * FROM openair_task_recovery_requests
                           WHERE request_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s FOR UPDATE""",
                        (request_id, task_id, owner_id, workspace_id),
                    )
                    request = cur.fetchone()
                    if not request:
                        raise RecoveryError(404, "recovery_not_found")
                    source = self._locked_source(
                        cur, task_id=task_id, source_run_id=request["source_run_id"], owner_id=owner_id, workspace_id=workspace_id,
                    )
                    if request["state"] == "confirmed":
                        return self._response(cur, request=request, task=task, source=source, replayed=True)
                    if request["state"] == "cancelled":
                        raise RecoveryError(409, "recovery_terminal")
                    request = self._create_successor(cur, task=task, source=source, request=request)
                    return self._response(cur, request=request, task=task, source=source, replayed=False)
        except psycopg.errors.UniqueViolation as exc:
            raise RecoveryError(409, "recovery_successor_exists") from exc
        except psycopg.Error as exc:
            raise RecoveryError(409, "recovery_preflight_failed") from exc

    def get(self, *, task_id: str, request_id: str, owner_id: str, workspace_id: str) -> dict[str, Any]:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM openair_tasks
                           WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                             AND status <> 'deleted'""",
                        (task_id, owner_id, workspace_id),
                    )
                    task = cur.fetchone()
                    if not task:
                        raise RecoveryError(404, "recovery_not_found")
                    cur.execute(
                        """SELECT * FROM openair_task_recovery_requests
                           WHERE request_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s""",
                        (request_id, task_id, owner_id, workspace_id),
                    )
                    request = cur.fetchone()
                    if not request:
                        raise RecoveryError(404, "recovery_not_found")
                    cur.execute(
                        """SELECT * FROM openair_execution_runs
                           WHERE run_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s""",
                        (request["source_run_id"], task_id, owner_id, workspace_id),
                    )
                    source = cur.fetchone()
                    if not source:
                        raise RecoveryError(404, "recovery_not_found")
                    return self._response(cur, request=request, task=task, source=source, replayed=False)
        except psycopg.Error as exc:
            raise RecoveryError(409, "recovery_preflight_failed") from exc

    def discover(
        self,
        *,
        task_id: str,
        source_run_id: str,
        expected_task_revision: int,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        """Find the durable request for one legacy recovery fence.

        The tuple is already protected by the recovery mutex/uniqueness rules,
        so discovery cannot choose between competing successors or create one.
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT * FROM openair_tasks
                           WHERE task_id=%s AND owner_id=%s AND workspace_id=%s
                             AND status <> 'deleted'""",
                        (task_id, owner_id, workspace_id),
                    )
                    task = cur.fetchone()
                    if not task:
                        raise RecoveryError(404, "recovery_not_found")
                    cur.execute(
                        """SELECT * FROM openair_task_recovery_requests
                           WHERE task_id=%s AND source_run_id=%s AND expected_task_revision=%s
                             AND owner_id=%s AND workspace_id=%s
                           ORDER BY created_at DESC, request_id DESC LIMIT 1""",
                        (
                            task_id,
                            source_run_id,
                            expected_task_revision,
                            owner_id,
                            workspace_id,
                        ),
                    )
                    request = cur.fetchone()
                    if not request:
                        raise RecoveryError(404, "recovery_not_found")
                    cur.execute(
                        """SELECT * FROM openair_execution_runs
                           WHERE run_id=%s AND task_id=%s AND owner_id=%s AND workspace_id=%s""",
                        (source_run_id, task_id, owner_id, workspace_id),
                    )
                    source = cur.fetchone()
                    if not source:
                        raise RecoveryError(404, "recovery_not_found")
                    return self._response(cur, request=request, task=task, source=source, replayed=True)
        except psycopg.Error as exc:
            raise RecoveryError(409, "recovery_preflight_failed") from exc

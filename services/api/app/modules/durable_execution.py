"""PostgreSQL-owned durable first-execution state and worker protocol.

Workers are deliberately API clients.  This module is the only place that
mutates the run tables and it is called only from API routes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.modules.artifact_repository import ensure_initial_version_truth
from app.modules.artifact_write import artifact_write_service
from app.modules.durable_artifact_presentation import validate_skill_presentation
from app.modules.postgres_migrations import apply_postgres_migrations
from app.modules.research_progress_projection import stamp_progress_projection


class DurableExecutionError(RuntimeError):
    def __init__(self, status_code: int, detail: str, *, run_id: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.run_id = run_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _initial_artifact_version_truth(
    *,
    artifact_id: str,
    created_by: str,
    storage_type: str,
    storage_ref: str,
    storage_scope: str | None,
    content_sha256: str,
    content_size: int,
) -> dict[str, Any]:
    """Build canonical V1 metadata at the durable Artifact creation boundary."""
    artifact = ensure_initial_version_truth({
        "id": artifact_id,
        "sourceTaskId": storage_scope if storage_type == "local" else None,
        "createdBy": created_by,
        "createdAt": _iso(_utc_now()),
        "storageType": storage_type,
        "storageScope": storage_scope,
        "path": storage_ref if storage_type == "local" else None,
        "externalKey": storage_ref if storage_type == "external" else None,
        "contentSha256": content_sha256,
        "contentSize": content_size,
    })
    return {
        "currentVersionId": artifact.get("currentVersionId"),
        "versions": artifact.get("versions") or [],
    }


def _fingerprint(task: dict[str, Any], source_mode: str) -> str:
    # The browser cannot choose execution identity.  The immutable accepted task
    # projection is the request body for initial_start.
    body = {
        "taskId": task.get("taskId"),
        "creationStatus": task.get("creationStatus"),
        "currentStage": task.get("currentStage"),
        "targetSubstage": _target_substage(task),
        "sourceMode": source_mode,
    }
    return hashlib.sha256(_json(body).encode("utf-8")).hexdigest()


def _target_substage(task: dict[str, Any]) -> str | None:
    stage = task.get("currentStage")
    for detail in task.get("stageDetails") or []:
        if detail.get("stageKey") != stage:
            continue
        for substage in detail.get("subStages") or []:
            if substage.get("status") in {"not_started", "running"} and substage.get("subStage"):
                return str(substage["subStage"])
    return None


def _validated_presentation(value: Any, *, expected_provider_id: str) -> dict[str, Any]:
    """Accept only the compact provider result that may become durable evidence."""
    if not isinstance(value, dict):
        raise DurableExecutionError(422, "invalid_artifact_presentation")
    title = str(value.get("title") or "").strip()
    mime_type = str(value.get("mimeType") or "").strip().lower()
    metadata = value.get("metadata")
    content_sha256 = str(value.get("contentSha256") or "").strip().lower()
    content_size = value.get("contentSize")
    if (
        not title
        or len(title) > 240
        or any(ord(char) < 32 for char in title)
        or mime_type not in {"text/markdown", "text/plain"}
        or not isinstance(metadata, dict)
        or set(metadata) != {"providerId", "adapter", "contentLength"}
        or metadata.get("providerId") != expected_provider_id
        or metadata.get("adapter") != "huiyan_chat_completions"
        or not isinstance(metadata.get("contentLength"), int)
        or metadata["contentLength"] < 1
        or not isinstance(content_size, int)
        or content_size < 1
        or not re.fullmatch(r"[0-9a-f]{64}", content_sha256)
    ):
        raise DurableExecutionError(422, "invalid_artifact_presentation")
    return {
        "title": title,
        "mimeType": mime_type,
        "metadata": {
            "providerId": expected_provider_id,
            "adapter": "huiyan_chat_completions",
            "contentLength": metadata["contentLength"],
        },
        "contentSha256": content_sha256,
        "contentSize": content_size,
    }


def _validated_skill_presentation(value: Any) -> dict[str, Any]:
    try:
        return validate_skill_presentation(value)
    except ValueError as exc:
        raise DurableExecutionError(422, "invalid_skill_artifact_presentation") from exc


def _format_for_mime_type(mime_type: str) -> str:
    if mime_type == "text/yaml":
        return "yaml"
    if mime_type == "text/markdown":
        return "md"
    if mime_type == "application/json":
        return "json"
    return "txt"


class DurableExecutionRepository:
    @staticmethod
    def _lock_task(cur: Any, task_id: str) -> None:
        """Serialize every durable mutation for one task within this transaction.

        PostgreSQL row locks alone are insufficient here: claim/reconcile starts
        from a run, while stop starts from a task.  The transaction-scoped lock
        is acquired before either row and gives all task/run mutators one stable
        order: advisory task lock, task row, then run row.
        """
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (task_id,))

    def _locked_task(self, cur: Any, task_id: str) -> dict[str, Any] | None:
        self._lock_task(cur, task_id)
        cur.execute(
            "SELECT * FROM openair_tasks WHERE task_id = %s AND status <> 'deleted' FOR UPDATE",
            (task_id,),
        )
        return cur.fetchone()

    @staticmethod
    def _task_id_for_run(cur: Any, run_id: str) -> str | None:
        # This is deliberately an unlocked lookup.  The advisory lock obtained
        # immediately afterwards establishes the lock protocol before either
        # mutable row is locked.
        cur.execute("SELECT task_id FROM openair_execution_runs WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        return str(row["task_id"]) if row else None

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bootstrap(self, auto_apply_migrations: bool = True) -> None:
        """Ensure PostgreSQL schema for durable execution.

        Default ``auto_apply_migrations=True`` preserves historical auto-apply.
        When False: still opens the connect/commit transaction boundary, but
        never scans the migrations directory or calls ``apply_postgres_migrations``.
        Baseline tables remain the responsibility of Task/Artifact repository
        bootstrap; this gate does not rebuild an empty migration ledger.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                if auto_apply_migrations:
                    apply_postgres_migrations(cur)

    def _event(self, cur: Any, run: dict[str, Any], event_type: str, payload: dict[str, Any] | None = None) -> int:
        sequence = int(run["next_event_sequence"])
        cur.execute(
            "INSERT INTO openair_execution_run_events(run_id, sequence, type, payload) VALUES (%s, %s, %s, %s::jsonb)",
            (run["run_id"], sequence, event_type, _json(payload or {})),
        )
        cur.execute(
            "UPDATE openair_execution_runs SET next_event_sequence = next_event_sequence + 1 WHERE run_id = %s",
            (run["run_id"],),
        )
        run["next_event_sequence"] = sequence + 1
        return sequence

    def _reject(
        self, _cur: Any | None = None, *, run_id: str | None, reason: str, lease_epoch: int | None, worker_id: str | None,
        received_call_id: str | None = None,
    ) -> None:
        # Rejections must outlive the HTTP 4xx.  They cannot share the callback
        # transaction because that transaction is deliberately rolled back when
        # the caller is rejected.
        with self._connect() as audit_conn:
            with audit_conn.cursor() as audit_cur:
                audit_cur.execute(
                    """INSERT INTO openair_execution_callback_rejections
                       (rejection_id, run_id, received_call_id, reason, lease_epoch, worker_id)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (f"reject_{uuid4().hex}", run_id, received_call_id, reason, lease_epoch, worker_id),
                )

    def start_initial(
        self, *, task_id: str, owner_id: str, workspace_id: str, idempotency_key: str, source_mode: str,
    ) -> tuple[dict[str, Any], bool]:
        if not idempotency_key.strip():
            raise DurableExecutionError(428, "Idempotency-Key is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._lock_task(cur, task_id)
                cur.execute(
                    "SELECT task_id, owner_id, workspace_id, status, current_stage, payload, revision "
                    "FROM openair_tasks WHERE task_id = %s AND status <> 'deleted' FOR UPDATE",
                    (task_id,),
                )
                task_row = cur.fetchone()
                if not task_row:
                    raise DurableExecutionError(404, "task not found")
                if task_row["owner_id"] != owner_id or task_row["workspace_id"] != workspace_id:
                    raise DurableExecutionError(403, "task is outside request scope")
                payload = task_row["payload"] if isinstance(task_row["payload"], dict) else json.loads(task_row["payload"])
                payload["taskId"] = task_id
                fingerprint = _fingerprint(payload, source_mode)
                cur.execute(
                    """SELECT r.* FROM openair_execution_idempotency i
                       JOIN openair_execution_runs r ON r.run_id = i.run_id
                       WHERE i.owner_id = %s AND i.workspace_id = %s AND i.task_id = %s
                         AND i.operation = 'initial_start' AND i.idempotency_key = %s""",
                    (owner_id, workspace_id, task_id, idempotency_key),
                )
                prior = cur.fetchone()
                if prior:
                    if prior["request_fingerprint"] != fingerprint:
                        raise DurableExecutionError(422, "idempotency_key_request_fingerprint_conflict", run_id=prior["run_id"])
                    self._record_start_attempt_in_transaction(
                        cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                        idempotency_key=idempotency_key, outcome="idempotent_replay", canonical_run_id=prior["run_id"],
                    )
                    return prior, True
                cur.execute(
                    "SELECT run_id FROM openair_execution_runs WHERE task_id = %s AND run_kind = 'initial_start'",
                    (task_id,),
                )
                canonical = cur.fetchone()
                if canonical:
                    raise DurableExecutionError(409, "initial_execution_already_exists", run_id=canonical["run_id"])
                if task_row["status"] != "not_started" or payload.get("creationStatus") != "confirmed":
                    raise DurableExecutionError(409, "task must be not_started with creationStatus=confirmed")

                run_id = f"run_{uuid4().hex}"
                call_id = f"call_{uuid4().hex}"
                revision = int(task_row["revision"]) + 1
                # Execution start cannot transition the canonical Task lifecycle.
                payload["status"] = task_row["status"]
                payload["updatedAt"] = _iso(_utc_now())
                calls = list(payload.get("capabilityCalls") or [])
                calls.append({
                    "id": call_id,
                    "ownerId": owner_id,
                    "workspaceId": workspace_id,
                    "createdBy": owner_id,
                    "taskId": task_id,
                    "runId": run_id,
                    "name": "durable_initial_execution",
                    "status": "not_started",
                    "requestedAt": payload["updatedAt"],
                    "sourceMode": source_mode,
                    "runMode": source_mode,
                })
                payload["capabilityCalls"] = calls
                payload["revision"] = revision
                stamp_progress_projection(payload, next_revision=revision)
                cur.execute(
                    """SELECT command_id, payload, user_message FROM openair_controller_commands
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND kind = 'supplement_input' AND state = 'received'
                       ORDER BY created_at FOR UPDATE""",
                    (task_id, owner_id, workspace_id),
                )
                commands = cur.fetchall()
                input_snapshot = json.loads(_json(payload))
                if commands:
                    input_snapshot["controllerInputContext"] = [
                        str((row["payload"] or {}).get("text") or row["user_message"])
                        for row in commands
                    ]
                cur.execute(
                    """UPDATE openair_tasks
                       SET updated_at = NOW(), revision = %s, payload = %s::jsonb
                       WHERE task_id = %s AND revision = %s AND status <> 'deleted'""",
                    (revision, _json(payload), task_id, task_row["revision"]),
                )
                if cur.rowcount != 1:
                    raise DurableExecutionError(409, "task_revision_conflict")
                cur.execute(
                    """INSERT INTO openair_execution_runs
                       (run_id, task_id, owner_id, workspace_id, task_revision, run_number, run_kind,
                        idempotency_key, request_fingerprint, input_snapshot, target_substage, source_mode, capability_call_id, state)
                       VALUES (%s, %s, %s, %s, %s, 1, 'initial_start', %s, %s, %s::jsonb, %s, %s, %s, 'queued')
                       RETURNING *""",
                    (run_id, task_id, owner_id, workspace_id, revision, idempotency_key, fingerprint,
                     _json(input_snapshot), _target_substage(payload), source_mode, call_id),
                )
                run = cur.fetchone()
                if commands:
                    cur.execute(
                        """UPDATE openair_controller_commands
                           SET state = 'applied', applied_run_id = %s, updated_at = NOW()
                           WHERE command_id = ANY(%s) AND state = 'received'""",
                        (run_id, [row["command_id"] for row in commands]),
                    )
                cur.execute(
                    """INSERT INTO openair_execution_idempotency
                       (owner_id, workspace_id, task_id, operation, idempotency_key, request_fingerprint, run_id)
                       VALUES (%s, %s, %s, 'initial_start', %s, %s, %s)""",
                    (owner_id, workspace_id, task_id, idempotency_key, fingerprint, run_id),
                )
                cur.execute(
                    """INSERT INTO openair_execution_capability_calls
                       (call_id, run_id, task_id, state, source_mode)
                       VALUES (%s, %s, %s, 'queued', %s)""",
                    (call_id, run_id, task_id, source_mode),
                )
                self._event(cur, run, "start_requested", {"taskRevision": revision, "sourceMode": source_mode})
                # The accepted request audit is part of the exact same durable
                # transaction as run/call/idempotency creation.  A process can
                # die before the HTTP response, but it cannot leave a canonical
                # run without its accepted request fact.
                self._record_start_attempt_in_transaction(
                    cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                    idempotency_key=idempotency_key, outcome="accepted", canonical_run_id=run_id,
                )
                return run, False

    @staticmethod
    def _record_start_attempt_in_transaction(
        cur: Any, *, task_id: str, owner_id: str, workspace_id: str, idempotency_key: str,
        outcome: str, canonical_run_id: str | None,
    ) -> None:
        cur.execute(
            """INSERT INTO openair_execution_start_attempts
               (attempt_id, owner_id, workspace_id, task_id, idempotency_key_sha256, outcome, canonical_run_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                f"start_attempt_{uuid4().hex}", owner_id, workspace_id, task_id,
                hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(), outcome, canonical_run_id,
            ),
        )

    def record_start_attempt(
        self, *, task_id: str, owner_id: str, workspace_id: str, idempotency_key: str,
        outcome: str, canonical_run_id: str | None,
    ) -> None:
        """Persist a request-attempt audit after the command outcome is known.

        The audit has its own completed transaction: a rejected command rolls
        back its state transition, but cannot erase the incoming request fact.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._record_start_attempt_in_transaction(
                    cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                    idempotency_key=idempotency_key, outcome=outcome, canonical_run_id=canonical_run_id,
                )

    def get_run(self, run_id: str, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM openair_execution_runs WHERE run_id = %s AND owner_id = %s AND workspace_id = %s",
                    (run_id, owner_id, workspace_id),
                )
                return cur.fetchone()

    def load_active_worker_authority(
        self,
        run_id: str,
        worker_id: str,
        lease_epoch: int,
    ) -> dict[str, Any] | None:
        """Resolve the exact durable run/worker/epoch authorization fact.

        This is a read-side authentication lookup only.  Every mutating method
        still repeats the same lease fence under ``FOR UPDATE`` so a lease that
        changes between authentication and mutation cannot be used.
        """
        if not str(run_id or "").strip() or not str(worker_id or "").strip():
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT run_id, task_id, owner_id, lease_owner, lease_epoch,
                              lease_expires_at, state
                         FROM openair_execution_runs
                        WHERE run_id = %s""",
                    (run_id,),
                )
                run = cur.fetchone()
        if (
            run is None
            or run["state"] != "leased"
            or run["lease_owner"] != worker_id
            or int(run["lease_epoch"]) != int(lease_epoch)
            or run["lease_expires_at"] is None
            or run["lease_expires_at"] <= _utc_now()
        ):
            return None
        return {
            "run_id": str(run["run_id"]),
            "task_id": str(run["task_id"]),
            "owner_id": str(run["owner_id"]),
            "worker_id": str(run["lease_owner"]),
            "lease_epoch": int(run["lease_epoch"]),
            "lease_expires_at": run["lease_expires_at"],
        }

    def get_latest_run_for_task(self, task_id: str, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
        """Return the canonical user-visible run projection without mutating task state."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT * FROM openair_execution_runs
                       WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                         AND NOT (state = 'cancelled' AND failure_classification = 'stopped_by_user')
                       ORDER BY run_number DESC, created_at DESC
                       LIMIT 1""",
                    (task_id, owner_id, workspace_id),
                )
                return cur.fetchone()

    def list_events(self, run_id: str, owner_id: str, workspace_id: str, after: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT e.sequence, e.type, e.payload, e.occurred_at FROM openair_execution_run_events e
                       JOIN openair_execution_runs r ON r.run_id = e.run_id
                       WHERE e.run_id = %s AND r.owner_id = %s AND r.workspace_id = %s AND e.sequence > %s
                       ORDER BY e.sequence""",
                    (run_id, owner_id, workspace_id, after),
                )
                return cur.fetchall()

    def get_call(self, run_id: str, owner_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT c.* FROM openair_execution_capability_calls c
                       JOIN openair_execution_runs r ON r.run_id = c.run_id
                       WHERE c.call_id = r.capability_call_id AND c.run_id = %s
                         AND r.owner_id = %s AND r.workspace_id = %s""",
                    (run_id, owner_id, workspace_id),
                )
                return cur.fetchone()

    def list_artifacts(self, run_id: str, owner_id: str, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT a.artifact_id, a.source_execution_run_id, a.source_capability_call_id,
                              a.source_mode, a.title, a.format, a.mime_type, a.metadata,
                              a.storage_type, a.storage_ref, a.content_size, a.content_sha256
                       FROM openair_artifacts a
                       JOIN openair_execution_runs r ON r.run_id = a.source_execution_run_id
                       WHERE a.source_execution_run_id = %s AND r.owner_id = %s AND r.workspace_id = %s
                       ORDER BY a.created_at""",
                    (run_id, owner_id, workspace_id),
                )
                return cur.fetchall()

    def claim(
        self,
        worker_id: str,
        lease_seconds: int,
        *,
        not_before: datetime | None = None,
    ) -> dict[str, Any] | None:
        if not_before is not None:
            if not_before.tzinfo is None:
                raise ValueError("not_before must include a timezone")
            not_before = not_before.astimezone(timezone.utc)
        run_cutoff_sql = " AND r.created_at >= %s" if not_before is not None else ""
        cutoff_params: tuple[datetime, ...] = (not_before,) if not_before is not None else ()
        now = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Pick without row locks, then acquire the common task lock and
                # re-check under task -> run locks.  This avoids the historical
                # claim(run)->task vs callback(task)->run deadlock.
                cur.execute(
                    """SELECT r.run_id, r.task_id FROM openair_execution_runs r
                       WHERE r.state = 'leased' AND r.lease_expires_at <= %s
                         AND NOT EXISTS (
                           SELECT 1 FROM openair_execution_gates g
                           WHERE g.run_id = r.run_id AND g.state = 'pending'
                         )
                         AND EXISTS (
                           SELECT 1 FROM openair_execution_capability_calls c
                           WHERE c.run_id = r.run_id AND c.external_action_started
                             AND c.state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')
                         )
                       ORDER BY created_at""",
                    (now,),
                )
                for candidate in cur.fetchall():
                    task = self._locked_task(cur, candidate["task_id"])
                    cur.execute(
                        """SELECT * FROM openair_execution_runs r WHERE run_id = %s
                           AND state = 'leased' AND lease_expires_at <= %s
                           AND NOT EXISTS (
                             SELECT 1 FROM openair_execution_gates g
                             WHERE g.run_id = r.run_id AND g.state = 'pending'
                           )
                           AND EXISTS (
                             SELECT 1 FROM openair_execution_capability_calls c
                             WHERE c.run_id = r.run_id AND c.external_action_started
                               AND c.state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')
                           )
                           FOR UPDATE SKIP LOCKED""",
                        (candidate["run_id"], now),
                    )
                    abandoned = cur.fetchone()
                    if not abandoned:
                        continue
                    adopted_call = self._trusted_persisted_capability_result(cur, abandoned)
                    if adopted_call is not None:
                        epoch = int(abandoned["lease_epoch"]) + 1
                        expires = now + timedelta(seconds=lease_seconds)
                        cur.execute(
                            """UPDATE openair_execution_runs SET state = 'leased', lease_owner = %s, lease_epoch = %s,
                               lease_expires_at = %s, started_at = COALESCE(started_at, NOW())
                               WHERE run_id = %s RETURNING *""",
                            (worker_id, epoch, expires, abandoned["run_id"]),
                        )
                        leased = cur.fetchone()
                        cur.execute(
                            """UPDATE openair_execution_capability_calls SET fence_epoch = %s
                               WHERE call_id = %s AND run_id = %s""",
                            (epoch, adopted_call["call_id"], abandoned["run_id"]),
                        )
                        self._event(
                            cur, leased, "worker_adopted_persisted_capability_result",
                            {"workerId": worker_id, "leaseEpoch": epoch, "callId": adopted_call["call_id"]},
                        )
                        return leased
                    cur.execute(
                        """UPDATE openair_execution_runs SET state = 'abandoned', ended_at = NOW(), lease_expires_at = NULL,
                           failure_classification = 'external_result_unknown' WHERE run_id = %s RETURNING *""",
                        (abandoned["run_id"],),
                    )
                    terminal = cur.fetchone()
                    cur.execute(
                        """UPDATE openair_execution_capability_calls
                           SET state = 'abandoned', ended_at = COALESCE(ended_at, NOW()),
                               failure_classification = 'external_result_unknown'
                           WHERE run_id = %s
                             AND state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')""",
                        (abandoned["run_id"],),
                    )
                    cur.execute(
                        """UPDATE openair_execution_work_units
                           SET state = 'unknown', ended_at = COALESCE(ended_at, NOW())
                           WHERE run_id = %s
                             AND state NOT IN ('succeeded', 'failed', 'cancelled', 'unknown')""",
                        (abandoned["run_id"],),
                    )
                    if task:
                        self._project_task(
                            cur, task,
                            call_id=abandoned["capability_call_id"],
                            call_status="failed", unknown_external_result=True,
                        )
                    self._event(cur, terminal, "abandoned", {"reason": "external_result_unknown"})
                cur.execute(
                    f"""SELECT r.run_id, r.task_id FROM openair_execution_runs r
                       WHERE NOT EXISTS (
                         SELECT 1 FROM openair_execution_gates g
                         WHERE g.run_id = r.run_id AND g.state = 'pending'
                       ) AND (r.state = 'queued' OR (
                         r.state = 'leased' AND r.lease_expires_at <= %s
                         AND NOT EXISTS (
                           SELECT 1 FROM openair_execution_capability_calls c
                           WHERE c.run_id = r.run_id AND c.external_action_started
                             AND c.state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')
                         )
                       )){run_cutoff_sql}
                       ORDER BY created_at""",
                    (now, *cutoff_params),
                )
                for candidate in cur.fetchall():
                    self._locked_task(cur, candidate["task_id"])
                    cur.execute(
                        f"""SELECT * FROM openair_execution_runs r WHERE run_id = %s
                           AND NOT EXISTS (
                             SELECT 1 FROM openair_execution_gates g
                             WHERE g.run_id = r.run_id AND g.state = 'pending'
                           ) AND (state = 'queued' OR (
                             state = 'leased' AND lease_expires_at <= %s
                             AND NOT EXISTS (
                               SELECT 1 FROM openair_execution_capability_calls c
                               WHERE c.run_id = r.run_id AND c.external_action_started
                                 AND c.state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')
                             )
                           )){run_cutoff_sql}
                           FOR UPDATE SKIP LOCKED""",
                        (candidate["run_id"], now, *cutoff_params),
                    )
                    run = cur.fetchone()
                    if not run:
                        continue
                    epoch = int(run["lease_epoch"]) + 1
                    expires = now + timedelta(seconds=lease_seconds)
                    cur.execute(
                        """UPDATE openair_execution_runs SET state = 'leased', lease_owner = %s, lease_epoch = %s,
                           lease_expires_at = %s, started_at = COALESCE(started_at, NOW()) WHERE run_id = %s RETURNING *""",
                        (worker_id, epoch, expires, run["run_id"]),
                    )
                    leased = cur.fetchone()
                    self._event(cur, leased, "worker_claimed", {"workerId": worker_id, "leaseEpoch": epoch})
                    return leased
                return None

    @staticmethod
    def _trusted_persisted_capability_result(cur: Any, run: dict[str, Any]) -> dict[str, Any] | None:
        """Return one completed-but-unfinished call only when its DB closure is exact.

        This is intentionally stricter than a non-empty reference list.  A
        worker may adopt only the single live call whose persisted references,
        association rows, artifact ownership and summary all agree.  Every
        other expired outbound call remains an absorbing unknown result.
        """
        cur.execute(
            """SELECT * FROM openair_execution_capability_calls
               WHERE run_id = %s AND external_action_started
                 AND state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')
               FOR UPDATE""",
            (run["run_id"],),
        )
        calls = cur.fetchall()
        if len(calls) != 1:
            return None
        call = calls[0]
        refs = list(call["output_artifact_refs"] or [])
        if not refs or not str(call.get("result_summary") or "").strip():
            return None
        if len(refs) != len(set(refs)):
            return None
        cur.execute(
            """SELECT a.artifact_id
               FROM openair_artifacts a
               JOIN openair_execution_artifact_associations x ON x.artifact_id = a.artifact_id
               JOIN openair_artifact_uploads u ON u.upload_id = x.upload_id
               WHERE x.run_id = %s AND x.call_id = %s AND a.artifact_id = ANY(%s)
                 AND a.owner_id = %s AND a.workspace_id = %s AND a.source_task_id = %s
                 AND a.source_execution_run_id = %s AND a.source_capability_call_id = %s
                 AND a.source_mode = 'live' AND u.run_id = x.run_id AND u.capability_call_id = x.call_id
                 AND u.artifact_id = x.artifact_id AND u.state = 'confirmed' AND u.confirmed_at IS NOT NULL
               FOR UPDATE""",
            (
                run["run_id"], call["call_id"], refs, run["owner_id"], run["workspace_id"], run["task_id"],
                run["run_id"], call["call_id"],
            ),
        )
        found = {row["artifact_id"] for row in cur.fetchall()}
        return call if found == set(refs) else None

    def _leased_run(self, cur: Any, run_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
        cur.execute("SELECT * FROM openair_execution_runs WHERE run_id = %s FOR UPDATE", (run_id,))
        run = cur.fetchone()
        if not run:
            self._reject(cur, run_id=run_id, reason="unknown_run", lease_epoch=lease_epoch, worker_id=worker_id)
            raise DurableExecutionError(404, "unknown_run")
        if run["state"] == "cancelled" and run.get("failure_classification") == "stopped_by_user":
            self._reject(cur, run_id=run_id, reason="stop_won", lease_epoch=lease_epoch, worker_id=worker_id)
            raise DurableExecutionError(409, "stop_won")
        if run["state"] in {"succeeded", "failed", "cancelled", "abandoned", "superseded"}:
            self._reject(cur, run_id=run_id, reason="terminal_run", lease_epoch=lease_epoch, worker_id=worker_id)
            raise DurableExecutionError(409, "terminal_run")
        if run["state"] == "awaiting_human":
            self._reject(cur, run_id=run_id, reason="human_gate_pending", lease_epoch=lease_epoch, worker_id=worker_id)
            raise DurableExecutionError(409, "human_gate_pending")
        if run["state"] == "cancel_requested":
            self._reject(cur, run_id=run_id, reason="stop_won", lease_epoch=lease_epoch, worker_id=worker_id)
            raise DurableExecutionError(409, "stop_won")
        expires = run["lease_expires_at"]
        if run["state"] != "leased" or run["lease_owner"] != worker_id or int(run["lease_epoch"]) != lease_epoch or not expires or expires <= _utc_now():
            self._reject(cur, run_id=run_id, reason="stale_or_expired_lease", lease_epoch=lease_epoch, worker_id=worker_id)
            raise DurableExecutionError(409, "stale_or_expired_lease")
        return run

    def renew(self, run_id: str, worker_id: str, lease_epoch: int, lease_seconds: int) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                if task_id:
                    self._locked_task(cur, task_id)
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                expires = _utc_now() + timedelta(seconds=lease_seconds)
                cur.execute("UPDATE openair_execution_runs SET lease_expires_at = %s WHERE run_id = %s RETURNING *", (expires, run_id))
                updated = cur.fetchone()
                self._event(cur, updated, "lease_renewed", {"workerId": worker_id, "leaseEpoch": lease_epoch})
                return updated

    def read_orchestration_context(self, run_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
        """Read one frozen snapshot after the same lease fence as callbacks."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                if task_id:
                    self._locked_task(cur, task_id)
                return self._leased_run(cur, run_id, worker_id, lease_epoch)

    def record_orchestration_plan(
        self, run_id: str, worker_id: str, lease_epoch: int, plan: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Persist one idempotent planning audit fact without changing run state."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                if task_id:
                    self._locked_task(cur, task_id)
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'orchestration_plan_created'
                         AND payload ->> 'leaseEpoch' = %s
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id, str(lease_epoch)),
                )
                existing = cur.fetchone()
                if existing:
                    return dict((existing["payload"] or {}).get("plan") or {}), True
                self._event(
                    cur,
                    run,
                    "orchestration_plan_created",
                    {"workerId": worker_id, "leaseEpoch": lease_epoch, "plan": plan},
                )
                return plan, False

    def read_orchestration_execution_context(
        self, run_id: str, worker_id: str, lease_epoch: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read the exact fenced plan required to validate a frozen request."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                if task_id:
                    self._locked_task(cur, task_id)
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'orchestration_plan_created'
                         AND payload ->> 'leaseEpoch' = %s
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id, str(lease_epoch)),
                )
                plan_event = cur.fetchone()
                plan = dict((plan_event or {}).get("payload") or {}).get("plan")
                if not isinstance(plan, dict):
                    self._reject(cur, run_id=run_id, reason="orchestration_plan_required", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "orchestration_plan_required")
                return run, plan

    def begin_orchestration_execution(
        self, run_id: str, worker_id: str, lease_epoch: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fence and record the one irreversible provider invocation for a run.

        The returned values contain only PostgreSQL-owned state.  The caller
        constructs the frozen adapter request and invokes the provider only
        after this transaction has committed; a lost HTTP response must never
        permit a second external invocation.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                task = self._locked_task(cur, task_id) if task_id else None
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'orchestration_plan_created'
                         AND payload ->> 'leaseEpoch' = %s
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id, str(lease_epoch)),
                )
                plan_event = cur.fetchone()
                plan = dict((plan_event or {}).get("payload") or {}).get("plan")
                if not isinstance(plan, dict):
                    self._reject(cur, run_id=run_id, reason="orchestration_plan_required", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "orchestration_plan_required")
                cur.execute(
                    """SELECT 1 FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'orchestration_execution_started'
                         AND payload ->> 'leaseEpoch' = %s""",
                    (run_id, str(lease_epoch)),
                )
                if cur.fetchone():
                    self._reject(cur, run_id=run_id, reason="orchestration_already_started", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "orchestration_already_started")
                cur.execute(
                    """SELECT 1 FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'execution_started'
                         AND payload ->> 'leaseEpoch' = %s""",
                    (run_id, str(lease_epoch)),
                )
                execution_started = bool(cur.fetchone())
                if not execution_started:
                    if run["external_action_started"]:
                        self._reject(cur, run_id=run_id, reason="external_action_start_inconsistent", lease_epoch=lease_epoch, worker_id=worker_id)
                        raise DurableExecutionError(409, "external_action_start_inconsistent")
                    cur.execute(
                        "UPDATE openair_execution_runs SET external_action_started = TRUE WHERE run_id = %s RETURNING *",
                        (run_id,),
                    )
                    started = cur.fetchone()
                    cur.execute(
                        "UPDATE openair_execution_capability_calls SET state = 'running', started_at = COALESCE(started_at, NOW()) WHERE call_id = %s",
                        (run["capability_call_id"],),
                    )
                    if task:
                        self._project_task(cur, task, call_id=run["capability_call_id"], call_status="running")
                    self._event(
                        cur,
                        started,
                        "execution_started",
                        {"workerId": worker_id, "leaseEpoch": lease_epoch},
                    )
                else:
                    started = run
                self._event(
                    cur,
                    started,
                    "orchestration_execution_started",
                    {"workerId": worker_id, "leaseEpoch": lease_epoch},
                )
                return started, plan

    def fail_provider_unavailable(self, run_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
        """Record a preflight failure without changing external-action state."""
        return self._fail_provider(
            run_id,
            worker_id,
            lease_epoch,
            failure_classification="provider_unavailable",
            require_external_action_started=False,
        )

    def fail_provider_response(self, run_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
        """A received but invalid provider response is a known failed execution."""
        return self._fail_provider(
            run_id,
            worker_id,
            lease_epoch,
            failure_classification="provider_response_invalid",
            require_external_action_started=True,
        )

    def fail_artifact_storage(self, run_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
        """Close every active collaboration record after a verified result cannot be stored."""
        failure_classification = "artifact_storage_failed_after_provider_result"
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                task = self._locked_task(cur, task_id) if task_id else None
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                if not run["external_action_started"]:
                    self._reject(
                        cur,
                        run_id=run_id,
                        reason="artifact_storage_failure_before_execution",
                        lease_epoch=lease_epoch,
                        worker_id=worker_id,
                    )
                    raise DurableExecutionError(409, "artifact_storage_failure_before_execution")
                cur.execute(
                    """UPDATE openair_execution_runs
                       SET state = 'failed', ended_at = NOW(), lease_expires_at = NULL,
                           failure_classification = %s
                       WHERE run_id = %s RETURNING *""",
                    (failure_classification, run_id),
                )
                failed = cur.fetchone()
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = 'failed', ended_at = COALESCE(ended_at, NOW()),
                           failure_classification = %s
                       WHERE run_id = %s
                         AND state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')""",
                    (failure_classification, run_id),
                )
                cur.execute(
                    """UPDATE openair_execution_work_units
                       SET state = 'failed', ended_at = COALESCE(ended_at, NOW()),
                           summary = COALESCE(summary, %s)
                       WHERE run_id = %s
                         AND state NOT IN ('succeeded', 'failed', 'cancelled', 'unknown')""",
                    ("Artifact storage failed after the provider returned a verified result.", run_id),
                )
                if task:
                    self._project_task(
                        cur, task, call_id=run["capability_call_id"],
                        call_status="failed",
                    )
                self._event(cur, failed, "failed", {"failureClassification": failure_classification})
                return failed

    def record_provider_presentation(
        self, run_id: str, worker_id: str, lease_epoch: int, presentation: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist the API-validated provider result before any worker upload.

        This is the sole authority for artifact presentation. Workers may carry
        a copy to the upload request, but cannot replace it.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                if task_id:
                    self._locked_task(cur, task_id)
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                if not run["external_action_started"]:
                    self._reject(cur, run_id=run_id, reason="provider_presentation_before_execution", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "provider_presentation_before_execution")
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'orchestration_plan_created'
                         AND payload ->> 'leaseEpoch' = %s
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id, str(lease_epoch)),
                )
                plan_event = cur.fetchone()
                plan = dict((plan_event or {}).get("payload") or {}).get("plan")
                selected_provider = plan.get("selectedProvider") if isinstance(plan, dict) else None
                presentation = _validated_presentation(presentation, expected_provider_id=str(selected_provider or ""))
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'provider_presentation_accepted'
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id,),
                )
                existing = cur.fetchone()
                if existing:
                    accepted = dict((existing["payload"] or {})).get("presentation")
                    if accepted != presentation:
                        self._reject(cur, run_id=run_id, reason="provider_presentation_conflict", lease_epoch=lease_epoch, worker_id=worker_id)
                        raise DurableExecutionError(409, "provider_presentation_conflict")
                    return presentation
                self._event(
                    cur,
                    run,
                    "provider_presentation_accepted",
                    {"leaseEpoch": lease_epoch, "presentation": presentation},
                )
                return presentation

    def _fail_provider(
        self,
        run_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        failure_classification: str,
        require_external_action_started: bool,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                task = self._locked_task(cur, task_id) if task_id else None
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                if bool(run["external_action_started"]) != require_external_action_started:
                    self._reject(cur, run_id=run_id, reason="provider_failure_state_inconsistent", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "provider_failure_state_inconsistent")
                cur.execute(
                    """UPDATE openair_execution_runs SET state = 'failed', ended_at = NOW(), lease_expires_at = NULL,
                       failure_classification = %s WHERE run_id = %s RETURNING *""",
                    (failure_classification, run_id),
                )
                failed = cur.fetchone()
                cur.execute(
                    "UPDATE openair_execution_capability_calls SET state = 'failed', ended_at = NOW() WHERE call_id = %s",
                    (run["capability_call_id"],),
                )
                if task:
                    self._project_task(
                        cur, task, call_id=run["capability_call_id"],
                        call_status="failed",
                    )
                self._event(cur, failed, "failed", {"failureClassification": failure_classification})
                return failed

    def callback(self, run_id: str, worker_id: str, lease_epoch: int, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"execution_started", "fallback", "succeeded", "failed", "cancelled", "abandoned"}
        if event_type not in allowed:
            self._reject(None, run_id=run_id, reason="invalid_callback_body", lease_epoch=lease_epoch, worker_id=worker_id)
            raise DurableExecutionError(422, "invalid_callback_type")
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                task = self._locked_task(cur, task_id) if task_id else None
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                if event_type == "execution_started":
                    if run["external_action_started"]:
                        # A completed HTTP request may be lost in transit.  The
                        # same worker/fencing token therefore receives an
                        # explicit replay result, with no second event or
                        # projection write.
                        replay = dict(run)
                        replay["idempotent_replay"] = True
                        return replay
                    cur.execute("UPDATE openair_execution_runs SET external_action_started = TRUE WHERE run_id = %s RETURNING *", (run_id,))
                    updated = cur.fetchone()
                    cur.execute(
                        "UPDATE openair_execution_capability_calls SET state = 'running', started_at = COALESCE(started_at, NOW()) WHERE call_id = %s",
                        (run["capability_call_id"],),
                    )
                    if task:
                        self._project_task(cur, task, call_id=run["capability_call_id"], call_status="running")
                elif event_type != "fallback":
                    if event_type == "succeeded":
                        cur.execute(
                            """SELECT
                                 COUNT(*) FILTER (WHERE u.state = 'confirmed') AS confirmed_count,
                                 COUNT(*) FILTER (WHERE u.state <> 'confirmed') AS incomplete_count,
                                 COUNT(*) FILTER (
                                   WHERE u.state = 'confirmed'
                                     AND x.upload_id IS NOT NULL
                                     AND c.call_id IS NOT NULL
                                     AND a.artifact_id IS NOT NULL
                                 ) AS associated_count,
                                 EXISTS (
                                   SELECT 1 FROM openair_execution_capability_calls c2
                                   WHERE c2.run_id = %s AND c2.work_unit_id IS NOT NULL
                                 ) AS is_collaboration
                               FROM openair_artifact_uploads u
                               LEFT JOIN openair_execution_artifact_associations x
                                 ON x.upload_id = u.upload_id
                                AND x.run_id = u.run_id
                                AND x.call_id = u.capability_call_id
                                AND x.artifact_id = u.artifact_id
                               LEFT JOIN openair_execution_capability_calls c
                                 ON c.call_id = x.call_id AND c.run_id = u.run_id
                               LEFT JOIN openair_artifacts a
                                 ON a.artifact_id = x.artifact_id
                                AND a.source_execution_run_id = u.run_id
                                AND a.source_capability_call_id = x.call_id
                               WHERE u.run_id = %s""",
                            (run_id, run_id),
                        )
                        barrier = cur.fetchone()
                        confirmed_count = int(barrier["confirmed_count"] or 0)
                        incomplete_count = int(barrier["incomplete_count"] or 0)
                        associated_count = int(barrier["associated_count"] or 0)
                        is_collaboration = bool(barrier["is_collaboration"])
                        incomplete_work_units = 0
                        if is_collaboration:
                            cur.execute(
                                """SELECT COUNT(*) AS count FROM openair_execution_work_units
                                   WHERE run_id = %s AND state <> 'succeeded'""",
                                (run_id,),
                            )
                            incomplete_work_units = int(cur.fetchone()["count"])
                        if (
                            confirmed_count == 0
                            or incomplete_count != 0
                            or associated_count != confirmed_count
                            or incomplete_work_units != 0
                            or (not is_collaboration and confirmed_count != 1)
                        ):
                            self._reject(cur, run_id=run_id, reason="artifact_completion_barrier_not_met", lease_epoch=lease_epoch, worker_id=worker_id)
                            raise DurableExecutionError(409, "artifact_completion_barrier_not_met")
                    terminal = {"succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled", "abandoned": "abandoned"}[event_type]
                    cur.execute(
                        """UPDATE openair_execution_runs SET state = %s, ended_at = NOW(), lease_expires_at = NULL,
                           failure_classification = %s WHERE run_id = %s RETURNING *""",
                        (terminal, payload.get("failureClassification"), run_id),
                    )
                    updated = cur.fetchone()
                    call_state = "succeeded" if terminal == "succeeded" else terminal
                    cur.execute(
                        "UPDATE openair_execution_capability_calls SET state = %s, ended_at = NOW() WHERE call_id = %s",
                        (call_state, run["capability_call_id"]),
                    )
                    if task:
                        projection_call_status = "completed" if terminal == "succeeded" else "failed"
                        self._project_task(
                            cur, task, call_id=run["capability_call_id"],
                            call_status=projection_call_status,
                        )
                else:
                    # A fallback is only an append-only explanation.  The
                    # initial source mode is immutable on run/call/artifact.
                    updated = run
                # Worker payload may describe its result but cannot impersonate
                # API-owned audit identity/fencing fields.
                safe_payload = {key: value for key, value in payload.items() if key not in {"workerId", "leaseEpoch"}}
                self._event(cur, updated, event_type, {**safe_payload, "workerId": worker_id, "leaseEpoch": lease_epoch})
                return updated

    def _project_task(
        self,
        cur: Any,
        task: dict[str, Any],
        *,
        call_id: str | None = None,
        call_status: str | None = None,
        artifact: dict[str, Any] | None = None,
        unknown_external_result: bool = False,
    ) -> dict[str, Any]:
        """Project execution details without owning the canonical Task lifecycle."""
        payload = self._task_payload(task)
        # Compatibility may enrich details but can only mirror canonical status.
        payload["status"] = task["status"]
        if call_id and call_status:
            calls = list(payload.get("capabilityCalls") or [])
            for call in calls:
                if call.get("id") == call_id:
                    call["status"] = call_status
                    if call_status in {"completed", "failed"}:
                        call["finishedAt"] = _iso(_utc_now())
                    if unknown_external_result:
                        call["resultSummary"] = (
                            "外部执行结果未知；系统持有恢复责任且不会自动重复提交。"
                        )
                    break
            payload["capabilityCalls"] = calls
        if unknown_external_result:
            payload["executionRecovery"] = {
                "state": "external_result_unknown", "owner": "system",
                "allowedActions": [],
                "message": "外部执行结果未知；系统将继续协调恢复，且不会自动重复提交。",
            }
        if artifact:
            payload["artifacts"] = [*(payload.get("artifacts") or []), artifact]
        payload["updatedAt"] = _iso(_utc_now())
        revision = int(task["revision"]) + 1
        payload["revision"] = revision
        stamp_progress_projection(payload, next_revision=revision)
        cur.execute(
            """UPDATE openair_tasks SET updated_at = NOW(), revision = %s, payload = %s::jsonb
               WHERE task_id = %s AND revision = %s AND status <> 'deleted'""",
            (revision, _json(payload), task["task_id"], task["revision"]),
        )
        if cur.rowcount != 1:
            raise DurableExecutionError(409, "task_revision_conflict")
        task["revision"] = revision
        task["payload"] = payload
        return payload

    def stop_current(
        self,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        *,
        stopped_by: str | None = None,
    ) -> dict[str, Any]:
        """Commit cancellation in the same durable state machine as completion.

        A worker completion requires ``leased`` and the current fencing token;
        this transition to ``cancel_requested`` therefore makes a committed stop
        win permanently without consulting a process-local registry.
        """
        actor = (stopped_by or owner_id).strip()
        if not actor or len(actor) > 240 or any(ord(char) < 32 for char in actor):
            raise DurableExecutionError(422, "invalid_stop_actor")
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._lock_task(cur, task_id)
                cur.execute(
                    "SELECT * FROM openair_tasks WHERE task_id = %s AND owner_id = %s AND workspace_id = %s AND status <> 'deleted' FOR UPDATE",
                    (task_id, owner_id, workspace_id),
                )
                task = cur.fetchone()
                if not task:
                    raise DurableExecutionError(404, "task not found")
                cur.execute(
                    """SELECT * FROM openair_execution_runs WHERE task_id = %s
                       ORDER BY run_number DESC, created_at DESC FOR UPDATE LIMIT 1""",
                    (task_id,),
                )
                run = cur.fetchone()
                if not run:
                    raise DurableExecutionError(409, "no_execution_run")
                if run["state"] in {"succeeded", "failed", "cancelled", "abandoned", "superseded"}:
                    raise DurableExecutionError(409, "already_terminal", run_id=run["run_id"])
                if run["state"] == "cancel_requested":
                    return self._task_payload(task)
                cur.execute(
                    """UPDATE openair_execution_gates
                       SET state = 'cancelled', resolution_action = 'terminate_run',
                           resolution_note = 'Stopped from task execution controls.',
                           resolved_by = %s, resolved_at = NOW()
                       WHERE run_id = %s AND state = 'pending'""",
                    (actor, run["run_id"]),
                )
                cur.execute(
                    """UPDATE openair_execution_runs SET state = 'cancel_requested', lease_expires_at = NULL
                       WHERE run_id = %s RETURNING *""",
                    (run["run_id"],),
                )
                cancellation_requested = cur.fetchone()
                self._event(cur, cancellation_requested, "stop_requested", {"reason": "user_stop"})
                cur.execute(
                    """UPDATE openair_execution_runs SET state = 'cancelled', ended_at = NOW(),
                       failure_classification = 'stopped_by_user' WHERE run_id = %s RETURNING *""",
                    (run["run_id"],),
                )
                stopped = cur.fetchone()
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET state = 'cancel_requested', ended_at = COALESCE(ended_at, NOW()),
                           failure_classification = 'stopped_by_user'
                       WHERE run_id = %s
                         AND state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')""",
                    (run["run_id"],),
                )
                cur.execute(
                    """UPDATE openair_execution_work_units
                       SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                       WHERE run_id = %s
                         AND state NOT IN ('succeeded', 'failed', 'cancelled', 'unknown')""",
                    (run["run_id"],),
                )
                self._event(cur, stopped, "cancelled", {"reason": "user_stop"})
                payload = self._project_task(
                    cur,
                    task,
                    call_id=run["capability_call_id"],
                    call_status="failed",
                )
                return payload

    @staticmethod
    def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
        payload = task["payload"] if isinstance(task["payload"], dict) else json.loads(task["payload"])
        payload = dict(payload)
        payload["taskId"] = task["task_id"]
        payload["revision"] = int(task["revision"])
        return payload

    def persist_collaboration_artifact(
        self,
        run_id: str,
        call_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        content: bytes,
        presentation: dict[str, Any],
        summary: str,
        related_skill_id: str | None = None,
        artifact_state: str = "needs_review",
    ) -> str:
        """Persist one verified live Agent result before its invocation can finish."""
        if not isinstance(content, bytes) or not content:
            raise DurableExecutionError(422, "collaboration_artifact_content_required")
        if artifact_state not in {"needs_review", "partial_success"}:
            raise DurableExecutionError(422, "collaboration_artifact_state_invalid")
        try:
            content_text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DurableExecutionError(422, "collaboration_artifact_must_be_utf8") from exc
        digest = hashlib.sha256(content).hexdigest()
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                task = self._locked_task(cur, task_id) if task_id else None
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                if run["source_mode"] != "live":
                    raise DurableExecutionError(409, "collaboration_artifact_requires_live_run")
                cur.execute(
                    """SELECT * FROM openair_execution_capability_calls
                       WHERE call_id = %s AND run_id = %s FOR UPDATE""",
                    (call_id, run_id),
                )
                call = cur.fetchone()
                if not call or not call["work_unit_id"] or not call["external_action_started"]:
                    raise DurableExecutionError(409, "collaboration_artifact_call_not_started")
                cur.execute(
                    """SELECT a.artifact_id, a.content_sha256
                       FROM openair_execution_artifact_associations x
                       JOIN openair_artifacts a ON a.artifact_id = x.artifact_id
                       WHERE x.run_id = %s AND x.call_id = %s""",
                    (run_id, call_id),
                )
                existing = cur.fetchone()
                if existing:
                    if existing["content_sha256"] != digest:
                        raise DurableExecutionError(409, "collaboration_artifact_result_conflict")
                    return existing["artifact_id"]
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'orchestration_plan_created'
                         AND payload ->> 'leaseEpoch' = %s
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id, str(lease_epoch)),
                )
                plan_event = cur.fetchone()
                plan = dict((plan_event or {}).get("payload") or {}).get("plan")
                selected_provider = plan.get("selectedProvider") if isinstance(plan, dict) else None
                accepted = (
                    _validated_skill_presentation(presentation)
                    if call["invocation_kind"] == "skill"
                    else _validated_presentation(presentation, expected_provider_id=str(selected_provider or ""))
                )
                if call["invocation_kind"] == "skill" and (
                    accepted["metadata"]["skillId"] != call["skill_id"]
                    or related_skill_id != call["skill_id"]
                ):
                    raise DurableExecutionError(422, "skill_artifact_related_skill_mismatch")
                if (
                    accepted["contentSize"] != len(content)
                    or accepted["contentSha256"] != digest
                    or (call["invocation_kind"] != "skill" and accepted["metadata"]["contentLength"] != len(content_text))
                ):
                    raise DurableExecutionError(422, "collaboration_artifact_checksum_mismatch")

        upload_id = f"upload_{uuid4().hex}"
        artifact_id = f"artifact_{uuid4().hex}"
        object_key = f"durable/{run_id}/{upload_id}"
        stored_blob = artifact_write_service.store_durable_bytes(
            task_id,
            object_key,
            content,
            accepted["mimeType"],
        )
        storage_projection = (
            {"storageType": "external", "storageScope": stored_blob.storage_scope, "externalKey": stored_blob.storage_ref}
            if stored_blob.storage_type == "external"
            else {"storageType": "local", "path": stored_blob.storage_ref}
        )

        with self._connect() as conn:
            with conn.cursor() as cur:
                task = self._locked_task(cur, task_id)
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                cur.execute(
                    """SELECT a.artifact_id, a.content_sha256
                       FROM openair_execution_artifact_associations x
                       JOIN openair_artifacts a ON a.artifact_id = x.artifact_id
                       WHERE x.run_id = %s AND x.call_id = %s""",
                    (run_id, call_id),
                )
                existing = cur.fetchone()
                if existing:
                    if existing["content_sha256"] != digest:
                        raise DurableExecutionError(409, "collaboration_artifact_result_conflict")
                    return existing["artifact_id"]
                cur.execute(
                    """SELECT * FROM openair_execution_capability_calls
                       WHERE call_id = %s AND run_id = %s FOR UPDATE""",
                    (call_id, run_id),
                )
                call = cur.fetchone()
                if not call or not call["external_action_started"] or int(call["fence_epoch"]) != lease_epoch:
                    raise DurableExecutionError(409, "stale_or_unstarted_capability_invocation")
                stored = (
                    artifact_write_service.read_durable_bytes(
                        task_id, stored_blob.storage_type, stored_blob.storage_ref, stored_blob.storage_scope
                    )
                    if stored_blob.storage_type == "external"
                    else artifact_write_service.read_durable_bytes(
                        task_id, stored_blob.storage_type, stored_blob.storage_ref
                    )
                )
                if stored != content:
                    raise DurableExecutionError(422, "artifact_upload_not_readable_or_checksum_mismatch")
                cur.execute(
                    """INSERT INTO openair_artifact_uploads
                       (upload_id, run_id, capability_call_id, lease_owner, lease_epoch, object_key, storage_scope,
                        expected_size, expected_sha256, state, artifact_id, confirmed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, NOW())""",
                    (
                        upload_id,
                        run_id,
                        call_id,
                        worker_id,
                        lease_epoch,
                        stored_blob.storage_ref,
                        stored_blob.storage_scope,
                        len(content),
                        digest,
                        artifact_id,
                    ),
                )
                version_truth = _initial_artifact_version_truth(
                    artifact_id=artifact_id,
                    created_by=run["owner_id"],
                    storage_type=stored_blob.storage_type,
                    storage_ref=stored_blob.storage_ref,
                    storage_scope=stored_blob.storage_scope,
                    content_sha256=digest,
                    content_size=len(content),
                )
                artifact_metadata = {
                    **accepted["metadata"],
                    "taskId": task_id,
                    "artifactState": artifact_state,
                    "sourceMode": "live",
                    **version_truth,
                }
                if call["invocation_kind"] == "skill":
                    if not isinstance(related_skill_id, str) or related_skill_id != call["skill_id"]:
                        raise DurableExecutionError(422, "skill_artifact_related_skill_mismatch")
                    artifact_metadata["relatedSkillId"] = related_skill_id
                cur.execute(
                    """INSERT INTO openair_artifacts
                       (artifact_id, owner_id, workspace_id, created_by, kind, origin, title, format, mime_type,
                        storage_type, storage_scope, storage_ref, content_size, content_sha256, source_task_id,
                        source_task_title, source_capability_call_id, metadata, created_at, updated_at,
                        source_execution_run_id, source_mode)
                        VALUES (%s, %s, %s, %s, 'execution_output', 'generated', %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW(), %s, 'live')""",
                    (
                        artifact_id, run["owner_id"], run["workspace_id"], run["owner_id"],
                        accepted["title"], _format_for_mime_type(accepted["mimeType"]), accepted["mimeType"],
                        stored_blob.storage_type, stored_blob.storage_scope, stored_blob.storage_ref, len(content), digest,
                        task_id,
                        (str(task.get("title") or "").strip() or None) if task else None,
                        call_id,
                        _json(artifact_metadata), run_id,
                    ),
                )
                cur.execute(
                    """INSERT INTO openair_execution_artifact_associations (upload_id, run_id, call_id, artifact_id)
                       VALUES (%s, %s, %s, %s)""",
                    (upload_id, run_id, call_id, artifact_id),
                )
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                       SET output_artifact_refs = %s::jsonb, result_summary = %s
                       WHERE call_id = %s""",
                    (_json([artifact_id]), summary, call_id),
                )
                self._project_task(
                    cur,
                    task,
                    call_id=call_id,
                    call_status="running",
                    artifact={
                        "id": artifact_id,
                        "taskId": task_id,
                        "sourceTaskId": task_id,
                        "ownerId": run["owner_id"],
                        "workspaceId": run["workspace_id"],
                        "createdBy": run["owner_id"],
                        "kind": "execution_output",
                        "title": accepted["title"],
                        "createdAt": _iso(_utc_now()),
                        "format": _format_for_mime_type(accepted["mimeType"]),
                        "mimeType": accepted["mimeType"],
                        "metadata": artifact_metadata,
                        **version_truth,
                        **storage_projection,
                        "contentSize": len(content),
                        "contentSha256": digest,
                        "sourceCapabilityCallId": call_id,
                        "sourceMode": "live",
                        "artifactState": artifact_state,
                        "relatedSkillId": artifact_metadata.get("relatedSkillId"),
                        "origin": "generated",
                    },
                )
                self._persist_declared_skill_files(
                    cur,
                    task=task,
                    run=run,
                    call=call,
                    task_id=task_id,
                    run_id=run_id,
                    artifact_state=artifact_state,
                )
                self._event(cur, run, "collaboration_artifact_confirmed", {"artifactId": artifact_id})
                return artifact_id

    def _persist_declared_skill_files(
        self,
        cur: Any,
        *,
        task: dict[str, Any] | None,
        run: dict[str, Any],
        call: dict[str, Any],
        task_id: str | None,
        run_id: str,
        artifact_state: str,
    ) -> None:
        """Register skill-declared output files (runtime_contract.artifact_files).

        Globs are relative to ``<workspace_root>/<task_id>/<work_unit_id>/``.
        Skills running in the durable worker write into the workspace volume
        shared with the API, so declared files (report markdown, figures,
        pareto CIFs, leaderboards) become platform artifacts the workbench
        renders natively (markdown/cif/png/svg/csv). Collection semantics
        live in ``app.execution.declared_skill_files`` (shared with the
        orchestration v1 dispatch path).
        """
        if not task_id or call.get("invocation_kind") != "skill" or not call.get("skill_id"):
            return
        from app.execution.declared_skill_files import (
            collect_declared_files,
            declared_file_segment,
            read_declared_file_bytes,
        )
        from app.execution.durable_skill_runtime import resolve_registered_durable_skill

        skill = resolve_registered_durable_skill(str(call["skill_id"]))
        source_contract = skill.get("sourceContract") if isinstance(skill, dict) else None
        contract = source_contract.get("runtime_contract") if isinstance(source_contract, dict) else None
        patterns = contract.get("artifact_files") if isinstance(contract, dict) else None
        ws_root = str(os.environ.get("OPENAIR_WORKSPACE_ROOT") or "").strip()
        if not ws_root:
            return
        work_unit_id = str(call.get("work_unit_id") or "")
        base = Path(ws_root) / declared_file_segment(task_id) / declared_file_segment(work_unit_id)
        files = collect_declared_files(ws_root, task_id, work_unit_id, patterns)
        if not files:
            return
        registered: dict[str, str] = {}
        for declared in files:
            rel = declared.rel
            full_path = declared.path
            fmt = declared.format
            mime_type = declared.mime_type
            file_bytes = read_declared_file_bytes(declared, base=base, task_id=task_id, registered=registered)
            if not file_bytes:
                continue
            file_artifact_id = f"artifact_{uuid4().hex}"
            file_upload_id = f"upload_{uuid4().hex}"
            object_key = f"durable/{run_id}/{file_upload_id}"
            stored_blob = artifact_write_service.store_durable_bytes(task_id, object_key, file_bytes, mime_type)
            digest = hashlib.sha256(file_bytes).hexdigest()
            version_truth = _initial_artifact_version_truth(
                artifact_id=file_artifact_id,
                created_by=run["owner_id"],
                storage_type=stored_blob.storage_type,
                storage_ref=stored_blob.storage_ref,
                storage_scope=stored_blob.storage_scope,
                content_sha256=digest,
                content_size=len(file_bytes),
            )
            metadata = {
                "taskId": task_id,
                "artifactState": artifact_state,
                "sourceMode": "live",
                "relatedSkillId": call["skill_id"],
                "declaredFile": rel,
                **version_truth,
            }
            storage_projection = (
                {"storageType": "external", "storageScope": stored_blob.storage_scope, "externalKey": stored_blob.storage_ref}
                if stored_blob.storage_type == "external"
                else {"storageType": "local", "path": stored_blob.storage_ref}
            )
            title = f"{call['skill_id']} · {rel.rsplit('/', 1)[-1]}"
            cur.execute(
                """INSERT INTO openair_artifacts
                   (artifact_id, owner_id, workspace_id, created_by, kind, origin, title, format, mime_type,
                    storage_type, storage_scope, storage_ref, content_size, content_sha256, source_task_id,
                    source_task_title, source_capability_call_id, metadata, created_at, updated_at,
                    source_execution_run_id, source_mode)
                   VALUES (%s, %s, %s, %s, 'execution_output', 'generated', %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW(), %s, 'live')""",
                (
                    file_artifact_id, run["owner_id"], run["workspace_id"], run["owner_id"],
                    title, fmt, mime_type,
                    stored_blob.storage_type, stored_blob.storage_scope, stored_blob.storage_ref, len(file_bytes), digest,
                    task_id,
                    (str(task.get("title") or "").strip() or None) if task else None,
                    call["call_id"],
                    _json(metadata), run_id,
                ),
            )
            cur.execute(
                """INSERT INTO openair_execution_artifact_associations (upload_id, run_id, call_id, artifact_id)
                   VALUES (%s, %s, %s, %s)""",
                (file_upload_id, run_id, call["call_id"], file_artifact_id),
            )
            cur.execute(
                """UPDATE openair_execution_capability_calls
                   SET output_artifact_refs = output_artifact_refs || %s::jsonb
                   WHERE call_id = %s""",
                (_json([file_artifact_id]), call["call_id"]),
            )
            if task is not None:
                self._project_task(
                    cur,
                    task,
                    call_id=call["call_id"],
                    call_status="running",
                    artifact={
                        "id": file_artifact_id,
                        "taskId": task_id,
                        "sourceTaskId": task_id,
                        "ownerId": run["owner_id"],
                        "workspaceId": run["workspace_id"],
                        "createdBy": run["owner_id"],
                        "kind": "execution_output",
                        "title": title,
                        "createdAt": _iso(_utc_now()),
                        "format": fmt,
                        "mimeType": mime_type,
                        "metadata": metadata,
                        **version_truth,
                        **storage_projection,
                        "contentSize": len(file_bytes),
                        "contentSha256": digest,
                        "sourceCapabilityCallId": call["call_id"],
                        "sourceMode": "live",
                        "artifactState": artifact_state,
                        "relatedSkillId": call["skill_id"],
                        "origin": "generated",
                    },
                )
            registered[rel] = file_artifact_id

    def request_upload(self, run_id: str, worker_id: str, lease_epoch: int, object_key: str | None,
                       expected_size: int | None, expected_sha256: str | None, capability_call_id: str | None,
                       presentation: dict[str, Any], storage_scope: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                if task_id:
                    self._locked_task(cur, task_id)
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                if capability_call_id and capability_call_id != run["capability_call_id"]:
                    self._reject(cur, run_id=run_id, reason="capability_call_not_owned_by_run", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "capability_call_not_owned_by_run")
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'provider_presentation_accepted'
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id,),
                )
                accepted_event = cur.fetchone()
                accepted_presentation = dict((accepted_event or {}).get("payload") or {}).get("presentation")
                if (
                    not isinstance(accepted_presentation, dict)
                    or presentation != accepted_presentation
                    or expected_size != accepted_presentation["contentSize"]
                    or expected_sha256 != accepted_presentation["contentSha256"]
                ):
                    self._reject(cur, run_id=run_id, reason="artifact_presentation_not_api_owned", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "artifact_presentation_not_api_owned")
                upload_id = f"upload_{uuid4().hex}"
                # Object identity is issued by the API and bound to this intent;
                # a worker cannot point an accepted artifact at an arbitrary key.
                object_key = f"durable/{run_id}/{upload_id}"
                cur.execute(
                    """INSERT INTO openair_artifact_uploads
                       (upload_id, run_id, capability_call_id, lease_owner, lease_epoch, object_key, storage_scope, expected_size, expected_sha256, state)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending') RETURNING *""",
                    (upload_id, run_id, capability_call_id or run["capability_call_id"], worker_id, lease_epoch, object_key, storage_scope, expected_size, expected_sha256),
                )
                upload = cur.fetchone()
                self._event(
                    cur,
                    run,
                    "artifact_upload_requested",
                    {"uploadId": upload_id, "objectKey": object_key},
                )
                return upload

    def confirm_upload(self, run_id: str, upload_id: str, worker_id: str, lease_epoch: int) -> dict[str, Any]:
        invalid = False
        with self._connect() as conn:
            with conn.cursor() as cur:
                task_id = self._task_id_for_run(cur, run_id)
                task = self._locked_task(cur, task_id) if task_id else None
                run = self._leased_run(cur, run_id, worker_id, lease_epoch)
                cur.execute("SELECT * FROM openair_artifact_uploads WHERE upload_id = %s AND run_id = %s FOR UPDATE", (upload_id, run_id))
                upload = cur.fetchone()
                if not upload:
                    self._reject(cur, run_id=run_id, reason="unknown_upload_intent", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(404, "unknown_upload_intent")
                cur.execute(
                    """SELECT payload FROM openair_execution_run_events
                       WHERE run_id = %s AND type = 'provider_presentation_accepted'
                       ORDER BY sequence DESC LIMIT 1""",
                    (run_id,),
                )
                accepted_event = cur.fetchone()
                presentation = dict((accepted_event or {}).get("payload") or {}).get("presentation")
                if not isinstance(presentation, dict):
                    self._reject(cur, run_id=run_id, reason="artifact_presentation_missing", lease_epoch=lease_epoch, worker_id=worker_id)
                    raise DurableExecutionError(409, "artifact_presentation_missing")
                # The worker's request body is not evidence that an object is
                # readable.  Read from object storage and calculate the digest
                # at the API boundary before any run can become successful.
                try:
                    from app.modules.minio_client import download_bytes, stat_object

                    metadata = stat_object(upload["object_key"], storage_scope=upload.get("storage_scope"))
                    content = (
                        download_bytes(upload["object_key"], storage_scope=upload.get("storage_scope"))
                        if metadata else None
                    )
                except Exception:
                    metadata = None
                    content = None
                actual_size = len(content) if content is not None else None
                actual_sha256 = hashlib.sha256(content).hexdigest() if content is not None else None
                try:
                    content_length = len(content.decode("utf-8")) if content is not None else None
                except UnicodeDecodeError:
                    content_length = None
                valid = metadata is not None and content is not None and (
                    upload["expected_size"] == presentation["contentSize"] == actual_size
                ) and (
                    upload["expected_sha256"] == presentation["contentSha256"] == actual_sha256
                ) and presentation["metadata"]["contentLength"] == content_length
                state = "confirmed" if valid else "failed"
                cur.execute("UPDATE openair_artifact_uploads SET state = %s, confirmed_at = NOW() WHERE upload_id = %s RETURNING *", (state, upload_id))
                updated = cur.fetchone()
                if valid:
                    artifact_id = f"artifact_{uuid4().hex}"
                    version_truth = _initial_artifact_version_truth(
                        artifact_id=artifact_id,
                        created_by=run["owner_id"],
                        storage_type="external",
                        storage_ref=upload["object_key"],
                        storage_scope=upload.get("storage_scope"),
                        content_sha256=actual_sha256,
                        content_size=actual_size,
                    )
                    artifact_metadata = {
                        "taskId": run["task_id"],
                        "artifactState": "needs_review",
                        "sourceMode": run["source_mode"],
                        **version_truth,
                    }
                    cur.execute(
                        """INSERT INTO openair_artifacts
                           (artifact_id, owner_id, workspace_id, created_by, kind, origin, title, format, mime_type,
                            storage_type, storage_scope, storage_ref, content_size, content_sha256, source_task_id,
                            source_task_title, source_capability_call_id, metadata, created_at, updated_at,
                            source_execution_run_id, source_mode)
                           VALUES (%s, %s, %s, %s, 'execution_output', 'generated', %s, %s, %s,
                                   'external', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW(), %s, %s)""",
                        (
                            artifact_id, run["owner_id"], run["workspace_id"], run["owner_id"],
                            presentation["title"], _format_for_mime_type(presentation["mimeType"]), presentation["mimeType"],
                            upload.get("storage_scope"), upload["object_key"], actual_size,
                            actual_sha256, run["task_id"],
                            (str(task.get("title") or "").strip() or None) if task else None,
                            upload["capability_call_id"],
                            _json(artifact_metadata),
                            run_id, run["source_mode"],
                        ),
                    )
                    cur.execute(
                        """INSERT INTO openair_execution_artifact_associations (upload_id, run_id, call_id, artifact_id)
                           VALUES (%s, %s, %s, %s)""",
                        (upload_id, run_id, upload["capability_call_id"], artifact_id),
                    )
                    cur.execute("UPDATE openair_artifact_uploads SET artifact_id = %s WHERE upload_id = %s", (artifact_id, upload_id))
                    updated["artifact_id"] = artifact_id
                    if task:
                        self._project_task(
                            cur,
                            task,
                            call_id=upload["capability_call_id"],
                            call_status="running",
                            artifact={
                                "id": artifact_id,
                                "taskId": run["task_id"],
                                "sourceTaskId": run["task_id"],
                                "ownerId": run["owner_id"],
                                "workspaceId": run["workspace_id"],
                                "createdBy": run["owner_id"],
                                "kind": "execution_output",
                                "title": presentation["title"],
                                "createdAt": _iso(_utc_now()),
                                "format": _format_for_mime_type(presentation["mimeType"]),
                                "mimeType": presentation["mimeType"],
                                "metadata": artifact_metadata,
                                **version_truth,
                                "storageType": "external",
                                "storageScope": upload.get("storage_scope"),
                                "externalKey": upload["object_key"],
                                "contentSize": actual_size,
                                "contentSha256": actual_sha256,
                                "sourceCapabilityCallId": upload["capability_call_id"],
                                "sourceMode": run["source_mode"],
                                "artifactState": "needs_review",
                                "origin": "generated",
                            },
                        )
                self._event(cur, run, "artifact_upload_confirmed" if valid else "artifact_upload_failed", {"uploadId": upload_id})
                if not valid:
                    invalid = True
        if invalid:
            # The failed intent/event committed above.  The HTTP response remains
            # non-2xx so a worker cannot mistake a failed verification for success.
            raise DurableExecutionError(422, "artifact_upload_not_readable_or_checksum_mismatch")
        return updated


def serialize_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": run["run_id"], "taskId": run["task_id"], "runNumber": run["run_number"],
        "runKind": run["run_kind"], "state": run["state"], "sourceMode": run["source_mode"],
        "leaseOwner": run["lease_owner"], "leaseEpoch": run["lease_epoch"],
        "capabilityCallId": run.get("capability_call_id"),
        "leaseExpiresAt": _iso(run["lease_expires_at"]), "targetSubstage": run["target_substage"],
        "latestEventSequence": int(run["next_event_sequence"]) - 1, "createdAt": _iso(run["created_at"]),
        "startedAt": _iso(run["started_at"]), "endedAt": _iso(run["ended_at"]),
        "failureClassification": run["failure_classification"],
        "idempotentReplay": bool(run.get("idempotent_replay", False)),
    }

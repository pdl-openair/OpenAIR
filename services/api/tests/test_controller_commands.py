from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository
from app.modules.controller_command_repository import (
    ControllerCommandRepository,
    is_supplement_eligible_primary_work_unit,
)
from app.routes import controller_commands


class FakeCommands:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], dict] = {}
        self.fingerprints: dict[tuple[str, str, str, str], tuple[str, str, int]] = {}
        self.revision = 4

    def create(
        self, *, task_id, owner_id, workspace_id, kind, text, expected_task_revision,
        idempotency_key, canonical_progression_available=True,
    ):
        if task_id != "task_1" or owner_id != "owner_1" or workspace_id != "workspace_1":
            raise DurableExecutionError(404, "task not found")
        key = (task_id, owner_id, workspace_id, idempotency_key)
        row = self.rows.get(key)
        fingerprint = (kind, text, expected_task_revision)
        if row:
            if self.fingerprints[key] != fingerprint:
                raise DurableExecutionError(409, "idempotency_key_request_fingerprint_conflict")
            return row, True
        if expected_task_revision != self.revision:
            raise DurableExecutionError(409, "controller_command_task_revision_conflict")
        if not canonical_progression_available:
            raise DurableExecutionError(409, "supplement_input_not_allowed")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        row = {
            "commandId": "cmd_1", "messageId": "msg_controller_command_cmd_1",
            "taskId": task_id, "kind": kind,
            "state": "received" if canonical_progression_available else "rejected",
            "userMessage": text,
            "submittedTaskRevision": expected_task_revision,
            "appliedExecutionDecisionId": None, "appliedWorkUnitId": None,
            "terminalReasonCode": (
                None if canonical_progression_available
                else "supplement_progression_service_unavailable"
            ),
            "submittedAt": now, "updatedAt": now,
        }
        self.revision += 1
        self.rows[key] = row
        self.fingerprints[key] = fingerprint
        return row, False

    def get_lifecycle_kind(self, *, task_id, command_id, owner_id, workspace_id):
        return "canonical_v1_2"

    def get_progression_context(self, *, task_id, command_id, owner_id, workspace_id):
        row = self.get(
            task_id=task_id, command_id=command_id, owner_id=owner_id, workspace_id=workspace_id
        )
        return {"taskStatus": "running"} if row is not None else None

    def reject_received(
        self, *, task_id, command_id, owner_id, workspace_id, reason_code
    ):
        row = self.get(
            task_id=task_id, command_id=command_id, owner_id=owner_id, workspace_id=workspace_id
        )
        if row and row["state"] == "received":
            row["state"] = "rejected"
            row["terminalReasonCode"] = reason_code
        return row

    def get(self, *, task_id, command_id, owner_id, workspace_id):
        for (row_task, row_owner, row_workspace, _), row in self.rows.items():
            if (row_task, row_owner, row_workspace, row["commandId"]) == (task_id, owner_id, workspace_id, command_id):
                return row
        return None

    def get_by_idempotency_key(self, *, task_id, idempotency_key, owner_id, workspace_id):
        return self.rows.get((task_id, owner_id, workspace_id, idempotency_key))


class RecordingCursor:
    """Exercise repository SQL branches without opening a PostgreSQL connection."""

    def __init__(self, *, task_status: str = "running", task_payload: dict | None = None,
                 boundary_claimable: bool = True, capability_call_exists: bool = False,
                 rows: list[dict] | None = None) -> None:
        self.task_status = task_status
        self.task_payload = task_payload or {}
        self.boundary_claimable = boundary_claimable
        self.capability_call_exists = capability_call_exists
        self.rows = rows or []
        self.statements: list[tuple[str, tuple]] = []
        self._one: dict | None = None
        self._many: list[dict] = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement: str, params: tuple | list | None = None) -> None:
        sql = " ".join(statement.split())
        values = tuple(params or ())
        self.statements.append((sql, values))
        self._one, self._many = None, []
        if "FROM openair_tasks" in sql and "FOR UPDATE" in sql:
            if self.task_status == "deleted" and "status <> 'deleted'" in sql:
                self._one = None
                return
            self._one = {
                "task_id": "task_1", "status": self.task_status, "revision": 0,
                "payload": deepcopy(self.task_payload),
            }
        elif "FROM openair_controller_commands" in sql and "idempotency_key=%s" in sql and "state='received' OR" not in sql:
            self._one = None
        elif sql.startswith("INSERT INTO openair_controller_commands"):
            self._one = {
                "command_id": values[0], "task_id": values[1], "owner_id": values[2],
                "workspace_id": values[3], "kind": values[4], "state": "received",
                "idempotency_key": values[6], "request_fingerprint": values[7],
                "payload": json.loads(values[5]),
                "user_message": values[8], "submitted_task_revision": values[9],
                "lifecycle_kind": values[10], "target_boundary": json.loads(values[11]),
                "applied_execution_decision_id": None, "applied_canonical_work_unit_id": None,
                "applied_work_unit_id": None,
                "terminal_reason_code": None, "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        elif "FROM openair_execution_runs r JOIN openair_execution_work_units" in sql:
            self._one = {"run_id": "run_1"} if self.boundary_claimable else None
        elif "FROM openair_controller_commands" in sql and "state='received' OR" in sql:
            run_id, work_unit_id = values[-2:]
            self._many = [
                row for row in self.rows
                if row["state"] == "received"
                or (row["state"] == "applied" and row.get("applied_run_id") == run_id and row.get("applied_work_unit_id") == work_unit_id)
            ]
        elif "FROM openair_execution_capability_calls" in sql:
            self._one = {"present": 1} if self.capability_call_exists else None
        elif sql.startswith("UPDATE openair_controller_commands SET state='applied'"):
            run_id, work_unit_id, command_ids = values
            for row in self.rows:
                if row["command_id"] in command_ids and row["state"] == "received":
                    row.update(state="applied", applied_run_id=run_id, applied_work_unit_id=work_unit_id)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class ControllerCreateStore:
    def __init__(self) -> None:
        self.task = {
            "task_id": "task_1", "owner_id": "owner_1", "workspace_id": "workspace_1",
            "status": "running", "revision": 4,
            "payload": {"revision": 4, "agentMessages": [{"id": "msg_existing", "role": "assistant"}]},
        }
        self.commands: dict[str, dict] = {}


class ControllerCreateCursor:
    """Small transactional model for the create/replay revision contract."""

    def __init__(self, store: ControllerCreateStore) -> None:
        self.store = store
        self._one: dict | None = None
        self._many: list[dict] = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement: str, params: tuple | list | None = None) -> None:
        sql = " ".join(statement.split())
        values = tuple(params or ())
        self._one, self._many, self.rowcount = None, [], 1
        if "FROM openair_tasks" in sql and "FOR UPDATE" in sql:
            self._one = self.store.task
        elif sql.startswith("INSERT INTO openair_controller_commands"):
            (command_id, task_id, owner_id, workspace_id, kind, _payload, key, fingerprint,
             text, submitted_revision, lifecycle_kind, target_boundary) = values
            self._one = {
                "command_id": command_id, "task_id": task_id, "owner_id": owner_id,
                "workspace_id": workspace_id, "kind": kind, "state": "received",
                "idempotency_key": key, "request_fingerprint": fingerprint, "user_message": text,
                "submitted_task_revision": submitted_revision, "lifecycle_kind": lifecycle_kind,
                "target_boundary": json.loads(target_boundary),
                "applied_execution_decision_id": None, "applied_canonical_work_unit_id": None,
                "applied_work_unit_id": None,
                "terminal_reason_code": None,
                "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
            }
            self.store.commands[key] = self._one
        elif sql.startswith("UPDATE openair_tasks"):
            next_revision, payload, task_id, owner_id, workspace_id, expected_revision = values
            task = self.store.task
            if (task_id, owner_id, workspace_id, expected_revision) == (
                task["task_id"], task["owner_id"], task["workspace_id"], task["revision"],
            ):
                task["revision"] = next_revision
                task["payload"] = json.loads(payload)
            else:
                self.rowcount = 0
        elif "FROM openair_controller_commands" in sql and "idempotency_key=%s" in sql:
            self._one = self.store.commands.get(values[-1])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class ControllerCreateConnection:
    def __init__(self, store: ControllerCreateStore) -> None:
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return ControllerCreateCursor(self.store)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def _repository(monkeypatch: pytest.MonkeyPatch, cursor: RecordingCursor) -> ControllerCommandRepository:
    repository = ControllerCommandRepository()
    monkeypatch.setattr(repository, "_connect", lambda: RecordingConnection(cursor))
    return repository


class RecordingMessageRepository:
    """Fake TaskMessageRepository capturing appends (E1: messages go to the
    table, not payload agentMessages)."""

    def __init__(self, recorded: list) -> None:
        self._recorded = recorded

    def append(self, cur, **kwargs):  # noqa: ANN001, ANN003 — test fake
        self._recorded.append(kwargs)
        return {"id": kwargs.get("message_uid"), "role": kwargs.get("role"),
                "text": kwargs.get("text"), "seq": 1}


def _patch_message_repo(monkeypatch: pytest.MonkeyPatch) -> list:
    from app.modules import controller_command_repository as ccr_mod
    recorded: list = []
    monkeypatch.setattr(ccr_mod, "TaskMessageRepository", lambda: RecordingMessageRepository(recorded))
    return recorded


def _command(command_id: str, *, state: str = "received", run_id: str | None = None,
             work_unit_id: str | None = None, text: str = "Keep the scope narrow.") -> dict:
    return {
        "command_id": command_id,
        "state": state,
        "payload": {"text": text},
        "user_message": text,
        "applied_run_id": run_id,
        "applied_work_unit_id": work_unit_id,
    }


class InitialStartStore:
    def __init__(self, *, fail_command_update: bool = False) -> None:
        self.task = {
            "task_id": "task_1", "owner_id": "owner_1", "workspace_id": "workspace_1",
            "status": "not_started", "current_stage": "problem_modeling", "revision": 0,
            "payload": {"creationStatus": "confirmed", "goalSummary": "Bound the research scope.", "capabilityCalls": []},
        }
        self.commands = [_command("cmd_1", text="Use only the supplied corpus.")]
        self.runs: dict[str, dict] = {}
        self.idempotency: dict[str, str] = {}
        self.fail_command_update = fail_command_update


class InitialStartCursor:
    def __init__(self, store: InitialStartStore) -> None:
        self.store = store
        self._one: dict | None = None
        self._many: list[dict] = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement: str, params: tuple | list | None = None) -> None:
        sql = " ".join(statement.split())
        values = tuple(params or ())
        self._one, self._many, self.rowcount = None, [], 1
        if "FROM openair_tasks WHERE task_id = %s" in sql and "FOR UPDATE" in sql:
            self._one = self.store.task
        elif "FROM openair_execution_idempotency i" in sql:
            run_id = self.store.idempotency.get(values[-1])
            self._one = self.store.runs.get(run_id) if run_id else None
        elif sql.startswith("SELECT run_id FROM openair_execution_runs"):
            self._one = next(({"run_id": run_id} for run_id, run in self.store.runs.items() if run["run_kind"] == "initial_start"), None)
        elif "FROM openair_controller_commands" in sql and "kind = 'supplement_input'" in sql:
            self._many = [row for row in self.store.commands if row["state"] == "received"]
        elif sql.startswith("UPDATE openair_tasks SET status = 'running'"):
            self.store.task["status"] = "running"
            self.store.task["revision"] = values[0]
        elif "INSERT INTO openair_execution_runs" in sql:
            run_id, task_id, owner_id, workspace_id, revision, key, fingerprint, snapshot, _target, source_mode, call_id = values
            run = {
                "run_id": run_id, "task_id": task_id, "owner_id": owner_id, "workspace_id": workspace_id,
                "task_revision": revision, "run_kind": "initial_start", "idempotency_key": key,
                "request_fingerprint": fingerprint, "input_snapshot": json.loads(snapshot), "source_mode": source_mode,
                "capability_call_id": call_id, "state": "queued", "next_event_sequence": 1,
            }
            self.store.runs[run_id] = run
            self._one = run
        elif sql.startswith("UPDATE openair_controller_commands"):
            if self.store.fail_command_update:
                raise RuntimeError("injected_controller_command_update_failure")
            run_id, command_ids = values
            for row in self.store.commands:
                if row["command_id"] in command_ids and row["state"] == "received":
                    row.update(state="applied", applied_run_id=run_id)
        elif "INSERT INTO openair_execution_idempotency" in sql:
            self.store.idempotency[values[3]] = values[5]

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class InitialStartConnection:
    def __init__(self, store: InitialStartStore) -> None:
        self.store = store
        self.before: dict | None = None

    def __enter__(self):
        self.before = deepcopy(self.store.__dict__)
        return self

    def __exit__(self, exc_type, *_args):
        if exc_type and self.before is not None:
            self.store.__dict__.clear()
            self.store.__dict__.update(self.before)
        return False

    def cursor(self):
        return InitialStartCursor(self.store)


def _durable_repository(monkeypatch: pytest.MonkeyPatch, store: InitialStartStore) -> DurableExecutionRepository:
    repository = DurableExecutionRepository()
    monkeypatch.setattr(repository, "_connect", lambda: InitialStartConnection(store))
    return repository


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    fake = FakeCommands()
    monkeypatch.setattr(controller_commands, "ControllerCommandRepository", lambda: fake)
    monkeypatch.setattr(
        controller_commands, "_canonical_supplement_progression_available", lambda: True
    )

    def progress(**kwargs):
        for row in fake.rows.values():
            if row["commandId"] == kwargs["command_id"]:
                row["state"] = "applied"
                row["appliedExecutionDecisionId"] = "decision_progressed"
                row["appliedWorkUnitId"] = "work_progressed"
                row["terminalReasonCode"] = None

    monkeypatch.setattr(controller_commands, "_trigger_canonical_supplement_progression", progress)
    app = FastAPI()
    app.state.fake_commands = fake
    app.include_router(controller_commands.router, prefix="/api")
    return TestClient(app)


def _headers(**extra: str) -> dict[str, str]:
    return {"X-OpenAIR-Owner-Id": "owner_1", "X-OpenAIR-Workspace-Id": "workspace_1", **extra}


def test_post_is_idempotent_and_rejects_fingerprint_conflict(client: TestClient) -> None:
    body = {"kind": "supplement_input", "text": "Limit scope.", "expectedTaskRevision": 4}
    first = client.post("/api/tasks/task_1/controller-commands", json=body, headers=_headers(**{"Idempotency-Key": "key_1"}))
    replay = client.post("/api/tasks/task_1/controller-commands", json=body, headers=_headers(**{"Idempotency-Key": "key_1"}))
    conflict = client.post("/api/tasks/task_1/controller-commands", json={**body, "text": "Different."}, headers=_headers(**{"Idempotency-Key": "key_1"}))
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["commandId"] == first.json()["commandId"]
    assert conflict.status_code == 409



def test_post_rejects_new_input_when_progression_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        controller_commands, "_canonical_supplement_progression_available", lambda: False
    )
    response = client.post(
        "/api/tasks/task_1/controller-commands",
        json={"kind": "supplement_input", "text": "Use the new constraint.", "expectedTaskRevision": 4},
        headers=_headers(**{"Idempotency-Key": "blocked-progression"}),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "supplement_input_not_allowed"
    assert client.app.state.fake_commands.rows == {}


def test_legacy_supplement_is_not_sent_to_canonical_progression(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = client.app.state.fake_commands
    monkeypatch.setattr(fake, "get_lifecycle_kind", lambda **_kwargs: "legacy_durable_run")
    monkeypatch.setattr(
        controller_commands,
        "_trigger_canonical_supplement_progression",
        lambda **_kwargs: pytest.fail("legacy command must not use canonical progression"),
    )

    response = client.post(
        "/api/tasks/task_1/controller-commands",
        json={"kind": "supplement_input", "text": "Legacy recovery input.", "expectedTaskRevision": 4},
        headers=_headers(**{"Idempotency-Key": "legacy-command"}),
    )

    assert response.status_code == 201
    assert response.json()["state"] == "received"

def test_post_requires_current_task_revision(client: TestClient) -> None:
    missing = client.post(
        "/api/tasks/task_1/controller-commands",
        json={"kind": "supplement_input", "text": "缺少版本。"},
        headers=_headers(**{"Idempotency-Key": "missing-revision"}),
    )
    stale = client.post(
        "/api/tasks/task_1/controller-commands",
        json={"kind": "supplement_input", "text": "旧版本。", "expectedTaskRevision": 3},
        headers=_headers(**{"Idempotency-Key": "stale-revision"}),
    )
    assert missing.status_code == 422
    assert stale.status_code == 409


def test_get_by_idempotency_key_discovers_committed_command_after_unknown_post_response(client: TestClient) -> None:
    headers = _headers(**{"Idempotency-Key": "unknown_response_key"})
    created = client.post(
        "/api/tasks/task_1/controller-commands",
        json={"kind": "supplement_input", "text": "Use the exact evidence set.", "expectedTaskRevision": 4},
        headers=headers,
    )
    discovered = client.get("/api/tasks/task_1/controller-commands", headers=headers)
    assert created.status_code == 201
    assert discovered.status_code == 200
    assert discovered.json() == created.json()


def test_public_json_is_strict_and_scope_mismatch_is_404(client: TestClient) -> None:
    response = client.post("/api/tasks/task_1/controller-commands", json={"kind": "supplement_input", "text": "Only cited sources.", "expectedTaskRevision": 4}, headers=_headers(**{"Idempotency-Key": "key_2"}))
    assert set(response.json()) == {"commandId", "messageId", "taskId", "kind", "state", "userMessage",
        "submittedTaskRevision", "appliedExecutionDecisionId", "appliedWorkUnitId",
        "terminalReasonCode", "submittedAt", "updatedAt"}
    other = client.get(f"/api/tasks/task_1/controller-commands/{response.json()['commandId']}", headers={"X-OpenAIR-Owner-Id": "owner_2", "X-OpenAIR-Workspace-Id": "workspace_2"})
    assert other.status_code == 404


@pytest.mark.parametrize("terminal_status", ["completed", "cancelled"])
def test_repository_create_rejects_terminal_task_before_insert(
    monkeypatch: pytest.MonkeyPatch, terminal_status: str,
) -> None:
    cursor = RecordingCursor(task_status=terminal_status)

    with pytest.raises(DurableExecutionError) as error:
        _repository(monkeypatch, cursor).create(
            task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
            kind="supplement_input", text="Do not expand the corpus.", expected_task_revision=0, idempotency_key="terminal-key",
        )

    assert error.value.status_code == 409
    assert error.value.detail == "controller_command_task_terminal"
    assert not any("INSERT INTO openair_controller_commands" in sql for sql, _ in cursor.statements)


def test_repository_create_hides_deleted_task_before_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = RecordingCursor(task_status="deleted")

    with pytest.raises(DurableExecutionError) as error:
        _repository(monkeypatch, cursor).create(
            task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
            kind="supplement_input", text="Deleted task", expected_task_revision=0, idempotency_key="deleted-key",
        )

    assert error.value.status_code == 404
    assert error.value.detail == "task not found"
    assert not any("INSERT INTO openair_controller_commands" in sql for sql, _ in cursor.statements)


def test_repository_create_persists_recovery_input_for_failed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = RecordingCursor(task_status="failed")
    _patch_message_repo(monkeypatch)

    row, replayed = _repository(monkeypatch, cursor).create(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        kind="supplement_input", text="Use the added boundary fact.", expected_task_revision=0, idempotency_key="recovery-key",
    )

    assert replayed is False
    assert row["taskId"] == "task_1"
    assert row["state"] == "received"
    assert row["userMessage"] == "Use the added boundary fact."


def test_repository_create_rejects_failed_canonical_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = RecordingCursor(
        task_status="failed",
        task_payload={"researchDesignVersions": [], "executionDecisions": [], "workUnits": []},
    )

    with pytest.raises(DurableExecutionError) as error:
        _repository(monkeypatch, cursor).create(
            task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
            kind="supplement_input", text="不应进入终态任务。",
            expected_task_revision=0, idempotency_key="canonical-terminal-key",
        )

    assert error.value.status_code == 409
    assert error.value.detail == "controller_command_task_terminal"
    assert not any("INSERT INTO openair_controller_commands" in sql for sql, _ in cursor.statements)



def test_canonical_create_is_rejected_before_insert_without_progression_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _r0c_controller_facts(monkeypatch, reason="required_input_missing")
    cursor = RecordingCursor(task_payload=_r0c_controller_source())
    recorded = _patch_message_repo(monkeypatch)
    with pytest.raises(DurableExecutionError) as error:
        _repository(monkeypatch, cursor).create(
            task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
            kind="supplement_input", text="仅使用新增边界。", expected_task_revision=0,
            idempotency_key="canonical-progression-blocked", canonical_progression_available=False,
        )
    assert error.value.status_code == 409
    assert error.value.detail == "supplement_input_not_allowed"
    assert not any(sql.startswith("INSERT INTO openair_controller_commands") for sql, _ in cursor.statements)
    assert recorded == []

def test_canonical_create_hides_command_from_legacy_recovery_but_keeps_authoritative_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _r0c_controller_facts(monkeypatch, reason="required_input_missing")
    cursor = RecordingCursor(task_payload=_r0c_controller_source())
    _patch_message_repo(monkeypatch)

    row, replayed = _repository(monkeypatch, cursor).create(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        kind="supplement_input", text="仅使用新增的实验边界。",
        expected_task_revision=0, idempotency_key="canonical-recovery-fence",
    )

    insert_values = next(values for sql, values in cursor.statements if sql.startswith("INSERT INTO openair_controller_commands"))
    assert replayed is False
    assert insert_values[8] == ""
    assert insert_values[10] == "canonical_v1_2"
    assert row["userMessage"] == "仅使用新增的实验边界。"


@pytest.mark.parametrize(
    "text",
    [
        "继续推进",
        "ok",
        "yes",
        "补充条件：温度保持 300 K，并仅使用新增证据。",
    ],
)
def test_repository_preserves_supplement_text_without_semantic_filtering(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> None:
    _r0c_controller_facts(monkeypatch, reason="required_input_missing")
    cursor = RecordingCursor(task_payload=_r0c_controller_source())
    recorded = _patch_message_repo(monkeypatch)

    row, replayed = _repository(monkeypatch, cursor).create(
        task_id="task_1",
        owner_id="owner_1",
        workspace_id="workspace_1",
        kind="supplement_input",
        text=text,
        expected_task_revision=0,
        idempotency_key=f"preserve-{len(text)}-{ord(text[0])}",
    )

    insert_values = next(
        values
        for sql, values in cursor.statements
        if sql.startswith("INSERT INTO openair_controller_commands")
    )
    stored_payload = json.loads(insert_values[5])

    assert replayed is False
    assert stored_payload == {"text": text}
    assert row["userMessage"] == text
    assert [message["text"] for message in recorded] == [text]


def test_repository_create_writes_message_and_matching_payload_revision_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ControllerCreateStore()
    repository = ControllerCommandRepository()
    monkeypatch.setattr(repository, "_connect", lambda: ControllerCreateConnection(store))
    recorded = _patch_message_repo(monkeypatch)

    first, replayed = repository.create(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        kind="supplement_input", text="Preserve this exact user input.", expected_task_revision=4, idempotency_key="revision-key",
    )
    replay, replayed_again = repository.create(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        kind="supplement_input", text="Preserve this exact user input.", expected_task_revision=4, idempotency_key="revision-key",
    )

    assert replayed is False
    assert replayed_again is True
    assert replay["commandId"] == first["commandId"]
    assert replay["messageId"] == first["messageId"]
    assert store.task["revision"] == 5
    assert store.task["payload"]["revision"] == store.task["revision"]
    # E1: the supplement message is persisted to openair_task_messages (captured
    # via the patched repo), not appended to the payload's agentMessages mirror.
    assert len(recorded) == 1
    assert recorded[0]["message_uid"] == first["messageId"]
    assert recorded[0]["role"] == "user"
    assert recorded[0]["text"] == "Preserve this exact user input."
    assert recorded[0]["message_type"] == "controller_supplement"
    assert [message["id"] for message in store.task["payload"]["agentMessages"]] == ["msg_existing"]


def test_canonical_migration_is_discovered_and_fences_legacy_application() -> None:
    from app.modules.postgres_migrations import MIGRATIONS_DIR

    migration = MIGRATIONS_DIR / "20260806_02_controller_commands_canonical.sql"
    down = MIGRATIONS_DIR / "20260806_02_controller_commands_canonical_down.sql"
    scanned = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql") if not path.name.endswith("_down.sql"))
    up_sql = migration.read_text(encoding="utf-8")
    down_sql = down.read_text(encoding="utf-8")

    assert migration.name in scanned
    assert "applied_canonical_work_unit_id TEXT" in up_sql
    assert "ADD COLUMN IF NOT EXISTS applied_work_unit_id" not in up_sql
    assert "openair_controller_commands_canonical_legacy_fence_check" in up_sql
    assert "DROP COLUMN IF EXISTS applied_canonical_work_unit_id" in down_sql


def test_provider_frozen_request_reads_the_applied_work_unit_instruction() -> None:
    from app.execution.research_dispatch_huiyan_provider import frozen_request_for_dispatch
    from app.execution.research_dispatch_provider import DispatchSubmissionRequest

    instruction = (
        "形成下一项结果\n\n用户在本工作单元冻结前补充的输入：\n"
        "- [controllerCommandId=command-a] 只使用新增边界。"
    )
    executor_ref = SimpleNamespace(executor_id="agent-a", definition_version=1)
    role = SimpleNamespace(value="primary")
    payload = {"workUnitId": "wu-next", "instruction": instruction}

    class WorkUnitView:
        def __init__(self) -> None:
            self.work_unit_id = "wu-next"
            self.assignment_id = "assignment-a"
            self.executor_ref = executor_ref
            self.registry_snapshot_ref = SimpleNamespace(content_hash="sha256:" + "a" * 64)
            self.allowed_skill_refs = []
            self.input_artifact_refs = []
            self.input_memory_refs = []
            self.research_design_version = 1
            self.substage_instance_id = "substage-a"
            self.instruction = instruction

        def model_dump(self, **_kwargs):
            return payload

    work_unit = WorkUnitView()
    assignment = SimpleNamespace(assignment_id="assignment-a", executor_ref=executor_ref, role=role)
    task = SimpleNamespace(
        work_units=[work_unit],
        execution_decisions=[SimpleNamespace(execution_decision_id="decision-a", assignments=[assignment])],
    )
    schema = {
        "contractId": "artifact-a",
        "semanticVersion": "1.0.0",
        "type": "object",
        "required": ["result"],
        "properties": {"result": {"type": "string"}},
    }
    digest = sha256(
        json.dumps(schema, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    snapshot = {
        "schemaVersion": 1,
        "definitions": {
            "agents": [{
                "agentId": "agent-a",
                "definitionVersion": 1,
                "enabled": True,
                "allowedProviders": ["openai_primary"],
                "outputContract": {
                    "frozenArtifactContract": {
                        "artifactContract": "artifact-a",
                        "artifactSemanticVersion": "1.0.0",
                        "artifactSchemaDigest": digest,
                        "artifactSchema": schema,
                        "artifactSchemaRef": "contracts/artifact-a.json",
                    }
                },
            }]
        },
    }

    class ProviderRepository:
        def load(self, **_scope):
            return task

        def load_registry_snapshot(self, _content_hash):
            return snapshot

    frozen = frozen_request_for_dispatch(
        DispatchSubmissionRequest(
            dispatch_id="dispatch-a", task_id="task_1", execution_decision_id="decision-a",
            work_unit_id="wu-next", idempotency_key="workunit:decision-a:assignment-a",
            owner_id="owner_1", workspace_id="workspace_1", payload=payload,
        ),
        provider_id="openai_primary",
        repository=ProviderRepository(),
    )

    assert frozen.frozenInputSnapshot["instruction"] == instruction
    assert "只使用新增边界。" in frozen.transport_payload()["frozenInputSnapshot"]["instruction"]
    assert "[controllerCommandId=command-a]" in frozen.frozenInputSnapshot["instruction"]


def _eligible_primary_case(
    *,
    role: str = "primary",
    executor_id: str = "research-agent",
    decision_sequence: object = 2,
    assembler: bool = False,
    revision_reason_code: str | None = None,
    selection_request_id: str | None = None,
    source_mode: str = "live",
    dispatchable: bool = True,
    status: str = "queued",
) -> tuple[dict, dict]:
    executor_ref = {"executorType": "agent", "executorId": executor_id, "definitionVersion": 1}
    assignment = {
        "assignmentId": "assignment-target",
        "role": role,
        "executorRef": executor_ref,
    }
    decision = {
        "executionDecisionId": "decision-target",
        "decisionSequence": decision_sequence,
        "taskId": "task_1",
        "status": "dispatched",
        "revisionReasonCode": revision_reason_code,
        "selectionRequestId": selection_request_id,
        "assignments": [assignment],
        "resultAssemblyPolicy": {
            "mode": "primary_output",
            "assemblerAssignmentId": assignment["assignmentId"] if assembler else None,
            "finalArtifactRequirements": [],
        },
    }
    work_unit = {
        "workUnitId": "wu-target",
        "taskId": "task_1",
        "executionDecisionId": decision["executionDecisionId"],
        "assignmentId": assignment["assignmentId"],
        "executorRef": executor_ref,
        "sourceMode": source_mode,
        "dispatchable": dispatchable,
        "status": status,
        "dispatchedAt": "2026-08-06T00:01:00Z",
        "instruction": "形成下一项结果",
    }
    return {"taskId": "task_1", "executionDecisions": [decision]}, work_unit


def test_supplement_eligibility_accepts_only_untouched_primary_research_unit() -> None:
    payload, work_unit = _eligible_primary_case()
    assert is_supplement_eligible_primary_work_unit(
        task_payload=payload,
        work_unit=work_unit,
        capability_call_work_unit_ids=set(),
        controller_command_id="command-a",
    )
    assert not is_supplement_eligible_primary_work_unit(
        task_payload=payload,
        work_unit=work_unit,
        capability_call_work_unit_ids={"wu-target"},
        controller_command_id="command-a",
    )


@pytest.mark.parametrize("decision_sequence", [None, "2", 2.0, True, False, {}])
def test_supplement_eligibility_rejects_non_integer_decision_sequence(decision_sequence: object) -> None:
    payload, work_unit = _eligible_primary_case(decision_sequence=decision_sequence)
    assert not is_supplement_eligible_primary_work_unit(
        task_payload=payload,
        work_unit=work_unit,
        capability_call_work_unit_ids=set(),
        controller_command_id="command-a",
    )


@pytest.mark.parametrize(
    ("role", "executor_id", "assembler", "revision_reason", "selection_request_id", "source_mode", "status"),
    [
        ("reviewer", "review-agent", False, None, None, "live", "queued"),
        ("support", "support-agent", False, None, None, "live", "queued"),
        ("primary", "assembler-agent", True, None, None, "live", "queued"),
        ("primary", "openair_summary_agent", False, None, None, "live", "queued"),
        ("primary", "result_reliability_audit", False, None, None, "live", "queued"),
        ("primary", "agent-recovery", False, "research_revision", "wrong-request", "live", "queued"),
        ("primary", "agent-projection", False, None, None, "legacy_projection", "queued"),
        ("primary", "research-agent", False, None, None, "live", "running"),
    ],
)
def test_supplement_eligibility_rejects_non_primary_or_already_started_units(
    role: str,
    executor_id: str,
    assembler: bool,
    revision_reason: str | None,
    selection_request_id: str | None,
    source_mode: str,
    status: str,
) -> None:
    payload, work_unit = _eligible_primary_case(
        role=role,
        executor_id=executor_id,
        assembler=assembler,
        revision_reason_code=revision_reason,
        selection_request_id=selection_request_id,
        source_mode=source_mode,
        status=status,
    )
    assert not is_supplement_eligible_primary_work_unit(
        task_payload=payload,
        work_unit=work_unit,
        capability_call_work_unit_ids=set(),
        controller_command_id="command-a",
    )


def test_research_revision_unit_requires_matching_controller_command_identity() -> None:
    payload, work_unit = _eligible_primary_case(
        revision_reason_code="research_revision",
        selection_request_id="controller-command:command-a:research-revision",
    )
    assert is_supplement_eligible_primary_work_unit(
        task_payload=payload,
        work_unit=work_unit,
        capability_call_work_unit_ids=set(),
        controller_command_id="command-a",
    )


def test_frozen_supplement_text_preserves_order_and_command_identity_without_duplicates() -> None:
    work_unit = {"workUnitId": "wu-target", "instruction": "形成下一项结果"}
    rows = [
        {"command_id": "command-a", "payload": {"text": "First."}, "user_message": ""},
        {"command_id": "command-b", "payload": {"text": "Second."}, "user_message": ""},
    ]
    frozen = ControllerCommandRepository._append_canonical_supplements(work_unit, rows)
    frozen_again = ControllerCommandRepository._append_canonical_supplements(frozen, rows)

    assert frozen_again == frozen
    assert frozen["instruction"].index("command-a") < frozen["instruction"].index("command-b")
    assert ControllerCommandRepository.command_ids_in_execution_payload(frozen) == ["command-a", "command-b"]


@pytest.mark.parametrize("failure", ["stale_lease", "expired_lease", "wrong_worker", "wrong_fence", "stop_requested", "terminal_run"])
def test_repository_boundary_rejects_invalid_claim_without_applying_received_command(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    cursor = RecordingCursor(boundary_claimable=False, rows=[_command("cmd_1")])

    with pytest.raises(DurableExecutionError) as error:
        _repository(monkeypatch, cursor).consume_at_work_unit_boundary(
            task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
            run_id="run_1", work_unit_id="wu_1", worker_id=f"worker_for_{failure}", fence_epoch=7,
        )

    assert error.value.status_code == 409
    assert error.value.detail == "controller_command_boundary_not_claimable"
    assert cursor.rows[0]["state"] == "received"
    assert not any(sql.startswith("UPDATE openair_controller_commands SET state='applied'") for sql, _ in cursor.statements)


def test_repository_boundary_recovers_only_its_own_applied_command(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = RecordingCursor(rows=[
        _command("cmd_same", state="applied", run_id="run_1", work_unit_id="wu_1", text="Resume this unit."),
        _command("cmd_other", state="applied", run_id="run_1", work_unit_id="wu_2", text="Do not move me."),
    ])

    result = _repository(monkeypatch, cursor).consume_at_work_unit_boundary(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        run_id="run_1", work_unit_id="wu_1", worker_id="worker_1", fence_epoch=7,
    )

    assert result == ["Resume this unit."]
    assert cursor.rows[0]["applied_work_unit_id"] == "wu_1"
    assert cursor.rows[1]["applied_work_unit_id"] == "wu_2"
    assert not any(sql.startswith("UPDATE openair_controller_commands SET state='applied'") for sql, _ in cursor.statements)


def test_repository_boundary_applies_received_command_to_the_claimed_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = RecordingCursor(rows=[_command("cmd_1")])

    result = _repository(monkeypatch, cursor).consume_at_work_unit_boundary(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        run_id="run_1", work_unit_id="wu_1", worker_id="worker_1", fence_epoch=7,
    )

    assert result == ["Keep the scope narrow."]
    assert cursor.rows[0]["state"] == "applied"
    assert cursor.rows[0]["applied_run_id"] == "run_1"
    assert cursor.rows[0]["applied_work_unit_id"] == "wu_1"


def test_repository_boundary_leaves_new_received_command_for_next_unit_when_call_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = RecordingCursor(capability_call_exists=True, rows=[_command("cmd_next")])

    result = _repository(monkeypatch, cursor).consume_at_work_unit_boundary(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        run_id="run_1", work_unit_id="wu_1", worker_id="worker_1", fence_epoch=7,
    )

    assert result == []
    assert cursor.rows[0]["state"] == "received"
    assert not any(sql.startswith("UPDATE openair_controller_commands SET state='applied'") for sql, _ in cursor.statements)


def test_start_initial_atomically_freezes_and_applies_received_controller_input(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InitialStartStore()
    repository = _durable_repository(monkeypatch, store)

    first, replayed = repository.start_initial(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1", idempotency_key="start-key", source_mode="replay",
    )
    replay, replayed_again = repository.start_initial(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1", idempotency_key="start-key", source_mode="replay",
    )

    assert not replayed and replayed_again
    assert first["input_snapshot"]["controllerInputContext"] == ["Use only the supplied corpus."]
    assert replay["run_id"] == first["run_id"]
    assert replay["input_snapshot"]["controllerInputContext"] == ["Use only the supplied corpus."]
    assert store.commands[0]["state"] == "applied"
    assert store.commands[0]["applied_run_id"] == first["run_id"]
    assert store.commands[0].get("applied_work_unit_id") is None


def test_start_initial_rolls_back_run_and_controller_command_when_application_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InitialStartStore(fail_command_update=True)

    with pytest.raises(RuntimeError, match="injected_controller_command_update_failure"):
        _durable_repository(monkeypatch, store).start_initial(
            task_id="task_1", owner_id="owner_1", workspace_id="workspace_1", idempotency_key="start-key", source_mode="replay",
        )

    assert store.runs == {}
    assert store.task["status"] == "not_started"
    assert store.commands[0]["state"] == "received"
    assert store.commands[0]["applied_run_id"] is None


def _r0c_controller_source():
    return {
        "taskId": "task_1", "status": "running", "revision": 0,
        "researchDesignVersions": [{"researchDesignId": "design-1", "version": 1}],
        "executionDecisions": [{"executionDecisionId": "decision_1", "decisionSequence": 1}],
        "workUnits": [{"workUnitId": "wu_1"}],
    }


def _r0c_controller_facts(monkeypatch, *, reason="provider_configuration", unknown=False):
    from app.modules import workbench_user_actions
    from tests.support.r0c_interruption import r0c_canonical

    user_owned = reason == "required_input_missing"
    task = r0c_canonical(
        reason=reason, task_status="running", runtime_status="needs_input" if user_owned else "blocked_recoverable",
        work_status="external_result_unknown" if unknown else "blocked",
        decision_status="closed" if user_owned else "dispatched",
        decision_outcome="needs_input" if user_owned else None,
    )
    monkeypatch.setattr(workbench_user_actions, "_canonical_task", lambda _payload: task)
    return task, workbench_user_actions


def test_r0c_stale_allowed_snapshot_cannot_commit_a_later_supplement(monkeypatch):
    _task, actions = _r0c_controller_facts(monkeypatch, reason="required_input_missing")
    source = {**_r0c_controller_source(), "revision": 4}
    assert actions.project_supplement_input_action(source, progression_available=True)["allowed"] is True
    store = ControllerCreateStore()
    store.task["payload"] = {**source, "revision": 5}
    store.task["revision"] = 5
    repository = ControllerCommandRepository()
    monkeypatch.setattr(repository, "_connect", lambda: ControllerCreateConnection(store))
    recorded = _patch_message_repo(monkeypatch)
    with pytest.raises(DurableExecutionError) as error:
        repository.create(
            task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
            kind="supplement_input", text="新增原始测量记录", expected_task_revision=4,
            idempotency_key="stale-input", canonical_progression_available=True,
        )
    assert error.value.status_code == 409
    assert error.value.detail == "controller_command_task_revision_conflict"
    assert store.commands == {}
    assert recorded == []
    assert store.task["revision"] == 5


def test_r0c_genuine_input_receipt_keeps_exact_text_and_waits_for_application(monkeypatch):
    _task, actions = _r0c_controller_facts(monkeypatch, reason="required_input_missing")
    source = _r0c_controller_source()
    assert actions.project_supplement_input_action(source, progression_available=True)["allowed"] is True
    cursor = RecordingCursor(task_payload=source)
    recorded = _patch_message_repo(monkeypatch)
    text = "原始测量温度为 300 K，仅使用已提交的三次测量。"
    row, replayed = _repository(monkeypatch, cursor).create(
        task_id="task_1", owner_id="owner_1", workspace_id="workspace_1", kind="supplement_input",
        text=text, expected_task_revision=0, idempotency_key="genuine-input", canonical_progression_available=True,
    )
    assert replayed is False
    assert row["state"] == "received"
    assert row["appliedWorkUnitId"] is None
    assert row["userMessage"] == text
    assert [message["text"] for message in recorded] == [text]
    inserted = next(values for sql, values in cursor.statements if sql.startswith("INSERT INTO openair_controller_commands"))
    assert json.loads(inserted[5]) == {"text": text}


class R0CCanonicalInputCursor:
    """Database boundary double; production methods decide binding and state."""
    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self.rowcount = 0

    def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        self.rowcount = 0
        if normalized.startswith("UPDATE openair_controller_commands SET target_boundary="):
            binding, command_id, _task, _owner, _workspace = params
            for row in self.rows:
                if row["command_id"] == command_id and row["state"] == "received":
                    row["target_boundary"].update(json.loads(binding))
                    self.rowcount += 1
        elif normalized.startswith("UPDATE openair_controller_commands SET state='applied'"):
            decision_id, _same_id, work_id, _task, _owner, _workspace, command_ids, bound_work, bound_decision = params
            for row in self.rows:
                boundary = row["target_boundary"]
                if (row["command_id"] in command_ids and row["state"] == "received"
                    and boundary.get("canonicalWorkUnitId") == bound_work
                    and boundary.get("canonicalExecutionDecisionId") == bound_decision):
                    row.update(state="applied", applied_execution_decision_id=decision_id, applied_canonical_work_unit_id=work_id)
                    self.rowcount += 1

    def fetchall(self):
        return [row for row in self.rows if row["state"] == "received"]


def test_r0c_submitted_input_reaches_the_safe_primary_work_and_is_not_applied_early():
    payload, work = _eligible_primary_case()
    text = "使用这份原始测量记录核对材料性能。"
    payload.update(status="running", revision=5, researchDesignVersions=[], workUnits=[work], capabilityCalls=[])
    row = {
        "command_id": "command-a", "state": "received", "submitted_task_revision": 4,
        "target_boundary": {"knownWorkUnitIds": ["wu-previous"]},
        "payload": {"text": text}, "user_message": "",
    }
    cursor = R0CCanonicalInputCursor([row])
    materialized = ControllerCommandRepository.apply_canonical_at_materialization(
        cursor, task_payload=payload, owner_id="owner_1", workspace_id="workspace_1",
    )
    assert row["state"] == "received"
    assert row["target_boundary"]["canonicalWorkUnitId"] == "wu-target"
    assert text in materialized["workUnits"][0]["instruction"]
    assert text not in payload["workUnits"][0]["instruction"]
    frozen, command_ids = ControllerCommandRepository.prepare_canonical_at_authorization(
        cursor, task_payload=materialized, work_unit_id="wu-target", owner_id="owner_1", workspace_id="workspace_1",
    )
    assert command_ids == ["command-a"]
    assert frozen["instruction"].count(text) == 1
    assert row["state"] == "received"
    ControllerCommandRepository.mark_canonical_applied_at_action_start(
        cursor, task_id="task_1", owner_id="owner_1", workspace_id="workspace_1",
        execution_decision_id="decision-target", work_unit_id="wu-target", command_ids=command_ids,
    )
    assert row["state"] == "applied"
    assert row["applied_canonical_work_unit_id"] == "wu-target"
    assert row["applied_execution_decision_id"] == "decision-target"

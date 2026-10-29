from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.controller_command_repository import ControllerCommandRepository
from app.routes import controller_commands


class _FakeCommands:
    def __init__(self) -> None:
        self.row: dict | None = None
        self.failure: dict | None = None

    def create(self, *, task_id, owner_id, workspace_id, kind, text,
               expected_task_revision, idempotency_key, canonical_progression_available=True):
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.row = {
            "commandId": "command-test", "messageId": "msg_controller_command_command-test",
            "taskId": task_id, "kind": kind, "state": "received", "userMessage": text,
            "submittedTaskRevision": expected_task_revision,
            "appliedExecutionDecisionId": None, "appliedWorkUnitId": None,
            "terminalReasonCode": None, "submittedAt": now, "updatedAt": now,
        }
        return self.row, False

    def get_lifecycle_kind(self, **_scope):
        return "canonical_v1_2"

    def get_progression_context(self, **_scope):
        return {"taskStatus": "running"}

    def claim_received_for_progression(self, **_scope):
        return self.row

    def record_progression_failure(self, *, error_code, **scope):
        self.failure = {"errorCode": error_code, **scope}
        return self.row

    def get(self, **_scope):
        return self.row


def _client(monkeypatch: pytest.MonkeyPatch, error: Exception) -> tuple[TestClient, _FakeCommands]:
    fake = _FakeCommands()
    monkeypatch.setattr(controller_commands, "ControllerCommandRepository", lambda: fake)
    monkeypatch.setattr(controller_commands, "_canonical_supplement_progression_available", lambda: True)

    def fail(**_kwargs):
        raise error

    monkeypatch.setattr(controller_commands, "_trigger_canonical_supplement_progression", fail)
    app = FastAPI()
    app.include_router(controller_commands.router, prefix="/api")
    return TestClient(app), fake


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (RuntimeError("research_design_version_missing"), "research_design_version_missing"),
        (Exception("OPENAI_API_KEY=do-not-store"), "internal_exception:builtins.exception"),
    ],
)
def test_progression_failure_remains_received_and_retains_only_safe_fact(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected_code: str,
) -> None:
    client, fake = _client(monkeypatch, error)
    response = client.post(
        "/api/tasks/task_1/controller-commands",
        json={"kind": "supplement_input", "text": "Continue from trusted state.", "expectedTaskRevision": 4},
        headers={
            "X-OpenAIR-Owner-Id": "owner_1",
            "X-OpenAIR-Workspace-Id": "workspace_1",
            "Idempotency-Key": "progression-failure-test",
        },
    )

    assert response.status_code == 201
    assert response.json()["state"] == "received"
    assert response.json()["terminalReasonCode"] is None
    assert fake.failure is not None
    assert fake.failure["errorCode"] == expected_code
    assert "do-not-store" not in json.dumps(fake.failure)


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self._one = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params):
        sql = " ".join(statement.split())
        self.statements.append((sql, tuple(params)))
        now = datetime.now(timezone.utc)
        self._one = {
            "command_id": "command-test", "task_id": "task_1", "kind": "supplement_input",
            "state": "received", "payload": {"text": "Continue."}, "user_message": "",
            "submitted_task_revision": 4, "applied_execution_decision_id": None,
            "applied_canonical_work_unit_id": None, "applied_work_unit_id": None,
            "terminal_reason_code": None, "created_at": now, "updated_at": now,
        }

    def fetchone(self):
        return self._one


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value


def test_repository_releases_lease_without_terminalizing(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _Cursor()
    repository = ControllerCommandRepository()
    monkeypatch.setattr(repository, "_connect", lambda: _Connection(cursor))

    row = repository.record_progression_failure(
        task_id="task_1", command_id="command-test",
        owner_id="owner_1", workspace_id="workspace_1",
        error_code="research_design_version_missing",
    )

    sql, params = cursor.statements[0]
    assert row is not None and row["state"] == "received"
    assert "state = 'rejected'" not in sql
    assert "progression_lease_owner = NULL" in sql
    assert "progression_last_error = %s" in sql
    assert "progression_next_attempt_at" in sql
    assert params[:2] == ("research_design_version_missing", 15)

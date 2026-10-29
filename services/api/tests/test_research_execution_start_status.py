from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json

from app.models.research_execution_start_status import (
    ResearchExecutionStartRequestStatusOut,
    ResearchExecutionStartStatusOut,
)
from app.modules.research_execution_start_status import (
    ResearchExecutionStartStatusReader,
    evaluate_current_execution_start_status,
)
from tests.support.research_lifecycle import build_confirmed_research_task


class _Settings:
    persistence_backend = "postgres"
    postgres_host = "unused"
    postgres_port = 5432
    postgres_database = "unused"
    postgres_user = "unused"
    postgres_password = "unused"


class _Cursor:
    def __init__(self, row, statements: list[tuple[str, tuple]]) -> None:
        self._row = row
        self._statements = statements

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement: str, params: tuple) -> None:
        self._statements.append((statement, params))

    def fetchone(self):
        if callable(self._row):
            return self._row(self._statements[-1][0], self._statements[-1][1])
        return self._row


class _Connection:
    def __init__(self, row, statements: list[tuple[str, tuple]]) -> None:
        self._row = row
        self._statements = statements

    def cursor(self):
        return _Cursor(self._row, self._statements)


@contextmanager
def _connection(row, statements):
    yield _Connection(row, statements)


def _reader(row, statements: list[tuple[str, tuple]]) -> ResearchExecutionStartStatusReader:
    return ResearchExecutionStartStatusReader(
        connection_factory=lambda: _connection(row, statements),
        runtime_settings=_Settings(),
    )


def _blocked_payload(reason_code: str, revision: int) -> dict:
    return {"reasonCode": reason_code, "facts": {"taskRevision": revision}}


def _confirmed_payload(revision: int) -> dict:
    payload = build_confirmed_research_task().model_dump(mode="json", by_alias=True)
    payload["revision"] = revision

    def retarget(value):
        if isinstance(value, dict):
            if "taskId" in value:
                value["taskId"] = "task-a"
            for item in value.values():
                retarget(item)
        elif isinstance(value, list):
            for item in value:
                retarget(item)

    retarget(payload)
    return payload


def test_current_task_authority_allows_clean_not_started_revision_without_request_identity() -> None:
    statements: list[tuple[str, tuple]] = []
    reader = _reader(
        {
            "payload": _confirmed_payload(9),
            "revision": 9,
            "current_job_state": None,
        },
        statements,
    )

    status = reader.read_current(
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
    )

    assert status == ResearchExecutionStartStatusOut(
        taskId="task-a",
        state="not_committed",
        taskRevision=9,
        startAllowed=True,
        responsibility="NONE",
        retryable=True,
    )
    sql, params = statements[0]
    assert "FOR UPDATE" not in sql.upper()
    assert "selectionRequestId" not in status.model_dump(mode="json", by_alias=True)
    assert params == ("task-a", "owner", "workspace")


def test_current_unknown_system_reason_blocks_without_frontend_reason_whitelist() -> None:
    reader = _reader(
        {
            "payload": _confirmed_payload(10),
            "revision": 10,
            "current_job_state": "blocked",
            "current_job_expected_revision": 10,
            "current_job_result_payload": _blocked_payload("future_integrity_reason", 10),
            "current_job_last_error_code": None,
        },
        [],
    )

    status = reader.read_current(
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
    )

    assert status.state == "blocked"
    assert status.taskRevision == 10
    assert status.blockerRevision == 10
    assert status.blockingReasonCode == "future_integrity_reason"
    assert status.startAllowed is False
    assert status.responsibility == "SYSTEM"
    assert status.retryable is False


def test_old_request_receipt_remains_old_revision_and_cannot_be_current_authority() -> None:
    statements: list[tuple[str, tuple]] = []
    blocked = "blocked:" + json.dumps(
        {
            "reasonCode": "old_system_integrity_reason",
            "facts": {"taskRevision": 10},
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    reader = _reader(
        {
            "execution_decision_id": None,
            "blocked_outcome": blocked,
            "queue_state": "blocked",
            "queue_expected_revision": 10,
            "queue_result_payload": _blocked_payload("old_system_integrity_reason", 10),
            "queue_last_error_code": None,
        },
        statements,
    )

    receipt = reader.read(
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="request-old",
    )

    assert receipt == ResearchExecutionStartRequestStatusOut(
        taskId="task-a",
        selectionRequestId="request-old",
        state="blocked",
        taskRevision=10,
        blockingReasonCode="old_system_integrity_reason",
    )
    payload = receipt.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "startAllowed" not in payload
    assert "responsibility" not in payload
    assert "retryable" not in payload
    assert statements[0][1][1] == hashlib.sha256(b"request-old").hexdigest()


def test_new_revision_without_current_blocker_is_not_locked_by_old_request_receipt() -> None:
    reader = _reader(
        {
            "payload": _confirmed_payload(11),
            "revision": 11,
            "current_job_state": None,
        },
        [],
    )

    current = reader.read_current(
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
    )

    assert current.taskRevision == 11
    assert current.startAllowed is True
    assert current.blockerRevision is None


def test_confirmed_but_non_current_design_cannot_authorize_start() -> None:
    payload = _confirmed_payload(11)
    payload["activeResearchDesignRef"] = None
    reader = _reader(
        {
            "payload": payload,
            "revision": 11,
            "current_job_state": None,
        },
        [],
    )

    current = reader.read_current(
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
    )

    assert current.state == "blocked"
    assert current.startAllowed is False
    assert current.blockingReasonCode == "current_design_activation_missing"
    assert current.responsibility == "SYSTEM"
    assert current.blockerRevision == 11


def test_write_form_evaluator_locks_task_and_reuses_same_current_authority() -> None:
    statements: list[tuple[str, tuple]] = []
    cursor = _Cursor(
        {
            "payload": _confirmed_payload(12),
            "revision": 12,
            "current_job_state": "blocked",
            "current_job_expected_revision": 12,
            "current_job_result_payload": _blocked_payload("future_system_reason", 12),
            "current_job_last_error_code": None,
        },
        statements,
    )

    status = evaluate_current_execution_start_status(
        cursor,
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        lock_task=True,
    )

    assert status.startAllowed is False
    assert status.responsibility == "SYSTEM"
    assert "FOR UPDATE OF task" in statements[0][0]


def test_request_receipt_for_missing_request_is_observational_not_committed() -> None:
    reader = _reader(
        {
            "execution_decision_id": None,
            "blocked_outcome": None,
            "queue_state": None,
            "queue_expected_revision": None,
            "queue_result_payload": None,
            "queue_last_error_code": None,
        },
        [],
    )

    receipt = reader.read(
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="request-new",
    )

    assert receipt.state == "not_committed"
    assert receipt.taskRevision is None

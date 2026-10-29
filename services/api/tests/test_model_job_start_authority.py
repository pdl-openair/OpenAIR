from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Any

import pytest

from app.modules.model_job_queue import (
    ModelJobContinueDenied,
    ModelJobQueueRepository,
    ModelJobStartDenied,
)
from app.modules.research_execution_start_status import (
    evaluate_current_execution_start_status,
)
from tests.support.research_lifecycle import build_confirmed_research_task


class _Db:
    def __init__(self, *, revision: int = 7) -> None:
        self.revision = revision
        self.payload = build_confirmed_research_task().model_dump(
            mode="json", by_alias=True
        )
        self.payload["revision"] = revision

        def retarget(value):
            if isinstance(value, dict):
                if "taskId" in value:
                    value["taskId"] = "task-a"
                for item in value.values():
                    retarget(item)
            elif isinstance(value, list):
                for item in value:
                    retarget(item)

        retarget(self.payload)
        self.jobs: dict[str, dict[str, Any]] = {}
        self.statements: list[str] = []


class _Cursor:
    def __init__(self, db: _Db) -> None:
        self.db = db
        self._row: dict[str, Any] | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement: str, params: tuple) -> None:
        normalized = " ".join(statement.split())
        self.db.statements.append(normalized)

        if normalized.startswith("SELECT revision FROM openair_tasks"):
            self._row = {"revision": self.db.revision}
            return

        if normalized.startswith("SELECT payload, revision, status FROM openair_tasks"):
            self._row = {
                "payload": self.db.payload,
                "revision": self.db.revision,
                "status": self.db.payload.get("status", "awaiting_confirmation"),
            }
            return

        if normalized.startswith("SELECT * FROM openair_model_jobs"):
            self._row = self.db.jobs.get(str(params[-1]))
            return

        if normalized.startswith("SELECT command_hash FROM openair_model_jobs"):
            existing = self.db.jobs.get(str(params[-1]))
            self._row = (
                {"command_hash": existing["command_hash"]}
                if existing is not None
                else None
            )
            return

        if normalized.startswith("SELECT task.payload, task.revision"):
            initiating_request_id = params[0] if len(params) > 3 else None
            current = [
                job for job in self.db.jobs.values()
                if job["job_kind"] == "execution_selection"
                and not (
                    job["job_id"] == initiating_request_id
                    and job["state"] in {"queued", "leased"}
                )
                and (
                    job["expected_revision"] == self.db.revision
                    or (job.get("result_payload") or {}).get("facts", {}).get("taskRevision") == self.db.revision
                )
            ]
            current.sort(key=lambda row: (0 if row["state"] == "blocked" else 1, -row["sequence"]))
            job = current[0] if current else None
            self._row = {
                "payload": self.db.payload,
                "revision": self.db.revision,
                "current_job_state": job["state"] if job else None,
                "current_job_expected_revision": job["expected_revision"] if job else None,
                "current_job_result_payload": job.get("result_payload") if job else None,
                "current_job_last_error_code": job.get("last_error_code") if job else None,
            }
            return

        if normalized.startswith("INSERT INTO openair_model_jobs"):
            (
                resource_kind,
                resource_id,
                owner_id,
                workspace_id,
                job_id,
                job_kind,
                expected_revision,
                command_hash,
                command_payload_json,
                runtime_source_version,
            ) = params
            row = {
                "resource_kind": resource_kind,
                "resource_id": resource_id,
                "owner_id": owner_id,
                "workspace_id": workspace_id,
                "job_id": job_id,
                "job_kind": job_kind,
                "expected_revision": expected_revision,
                "command_hash": command_hash,
                "command_payload": json.loads(command_payload_json),
                "runtime_source_version": runtime_source_version,
                "state": "queued",
                "result_payload": None,
                "last_error_code": None,
                "sequence": len(self.db.jobs) + 1,
            }
            self.db.jobs[str(job_id)] = row
            self._row = row
            return

        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, db: _Db) -> None:
        self.db = db

    def cursor(self):
        return _Cursor(self.db)


class _Repo(ModelJobQueueRepository):
    def __init__(self, db: _Db) -> None:
        self.db = db

    @contextmanager
    def _connect(self):
        yield _Connection(self.db)


def _enqueue(repo: ModelJobQueueRepository, request_id: str, revision: int) -> dict[str, Any]:
    return repo.enqueue(
        resource_kind="task",
        resource_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        job_id=request_id,
        job_kind="execution_selection",
        expected_revision=revision,
        command_payload={"requestKind": "explicit_start"},
    )


def _enqueue_continue(
    repo: ModelJobQueueRepository,
    request_id: str,
    revision: int,
) -> dict[str, Any]:
    return repo.enqueue(
        resource_kind="task",
        resource_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        job_id=request_id,
        job_kind="execution_selection",
        expected_revision=revision,
        command_payload={"requestKind": "continue_research"},
    )


def _blocker_job(request_id: str, revision: int, reason: str, sequence: int = 1) -> dict[str, Any]:
    return {
        "resource_kind": "task",
        "resource_id": "task-a",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "job_id": request_id,
        "job_kind": "execution_selection",
        "expected_revision": revision,
        "command_hash": f"sha256:{request_id}",
        "command_payload": {"requestKind": "explicit_start"},
        "state": "blocked",
        "result_payload": {"reasonCode": reason, "facts": {"taskRevision": revision}},
        "last_error_code": None,
        "sequence": sequence,
    }


def test_write_rechecks_after_prior_read_and_rejects_new_request_under_current_blocker() -> None:
    db = _Db(revision=7)
    read_cursor = _Cursor(db)
    before = evaluate_current_execution_start_status(
        read_cursor,
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        lock_task=False,
    )
    assert before.startAllowed is True

    db.jobs["blocking-request"] = _blocker_job(
        "blocking-request",
        7,
        "future_system_integrity_reason",
    )

    with pytest.raises(ModelJobStartDenied) as exc_info:
        _enqueue(_Repo(db), "brand-new-request", 7)

    denied = exc_info.value.start_status
    assert denied.taskRevision == 7
    assert denied.startAllowed is False
    assert denied.responsibility == "SYSTEM"
    assert denied.retryable is False
    assert "brand-new-request" not in db.jobs
    assert any("FOR UPDATE OF task" in sql for sql in db.statements)


def test_old_revision_blocker_does_not_lock_repaired_new_revision() -> None:
    db = _Db(revision=8)
    db.jobs["old-blocker"] = _blocker_job(
        "old-blocker",
        7,
        "old_system_integrity_reason",
    )

    inserted = _enqueue(_Repo(db), "revision-8-start", 8)

    assert inserted["job_id"] == "revision-8-start"
    assert inserted["state"] == "queued"
    assert len(db.jobs) == 2


def test_two_distinct_start_requests_create_at_most_one_current_initial_job_and_replay_is_idempotent() -> None:
    db = _Db(revision=9)
    repo = _Repo(db)

    first = _enqueue(repo, "client-a", 9)
    replay = _enqueue(repo, "client-a", 9)
    with pytest.raises(ModelJobStartDenied):
        _enqueue(repo, "client-b", 9)

    assert replay == first
    current = [
        row for row in db.jobs.values()
        if row["expected_revision"] == 9
        and row["command_payload"].get("requestKind") == "explicit_start"
    ]
    assert [row["job_id"] for row in current] == ["client-a"]


def test_continue_research_rechecks_server_fact_and_preserves_same_request_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules import workbench_user_actions

    projections: list[dict[str, Any]] = []

    def allowed(payload):
        projections.append(payload)
        return {"allowed": True, "executionStopped": False}

    monkeypatch.setattr(
        workbench_user_actions,
        "project_continue_research_action",
        allowed,
    )
    db = _Db(revision=9)
    repo = _Repo(db)

    first = _enqueue_continue(repo, "continue-a", 9)
    assert repo.is_exact_replay(
        resource_kind="task",
        resource_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        job_id="continue-a",
        job_kind="execution_selection",
        expected_revision=9,
        command_payload={"requestKind": "continue_research"},
    ) is True
    db.revision = 10
    replay = _enqueue_continue(repo, "continue-a", 9)

    assert replay == first
    assert len(projections) == 1
    assert projections[0]["revision"] == 9
    assert db.jobs["continue-a"]["command_payload"] == {
        "requestKind": "continue_research"
    }


@pytest.mark.parametrize(
    "action",
    [
        {"allowed": False, "executionStopped": False},
        {"allowed": True, "executionStopped": True},
    ],
)
def test_continue_research_rejects_forged_or_stopped_request(
    monkeypatch: pytest.MonkeyPatch,
    action: dict[str, bool],
) -> None:
    from app.modules import workbench_user_actions

    monkeypatch.setattr(
        workbench_user_actions,
        "project_continue_research_action",
        lambda _payload: action,
    )
    db = _Db(revision=9)

    with pytest.raises(ModelJobContinueDenied):
        _enqueue_continue(_Repo(db), "continue-denied", 9)

    assert "continue-denied" not in db.jobs


def test_current_revision_blocker_wins_over_old_request_history() -> None:
    db = _Db(revision=11)
    db.jobs["request-n"] = _blocker_job("request-n", 10, "old_reason", sequence=1)
    db.jobs["request-n1"] = _blocker_job("request-n1", 11, "new_system_reason", sequence=2)

    with pytest.raises(ModelJobStartDenied) as exc_info:
        _enqueue(_Repo(db), "request-new", 11)

    assert exc_info.value.start_status.taskRevision == 11
    assert exc_info.value.start_status.blockingReasonCode == "new_system_reason"


def _active_job(request_id: str, revision: int, state: str, sequence: int = 1) -> dict[str, Any]:
    return {
        "resource_kind": "task",
        "resource_id": "task-a",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "job_id": request_id,
        "job_kind": "execution_selection",
        "expected_revision": revision,
        "command_hash": f"sha256:{request_id}",
        "command_payload": {"requestKind": "explicit_start"},
        "state": state,
        "result_payload": None,
        "last_error_code": None,
        "sequence": sequence,
    }


def test_write_boundary_context_ignores_same_initiating_queued_job() -> None:
    db = _Db(revision=12)
    db.jobs["initiating-request"] = _active_job(
        "initiating-request",
        12,
        "queued",
    )

    status = evaluate_current_execution_start_status(
        _Cursor(db),
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        lock_task=True,
        initiating_selection_request_id="initiating-request",
    )

    assert status.startAllowed is True
    assert status.state == "not_committed"


def test_write_boundary_context_ignores_same_initiating_leased_job() -> None:
    db = _Db(revision=13)
    db.jobs["initiating-request"] = _active_job(
        "initiating-request",
        13,
        "leased",
    )

    status = evaluate_current_execution_start_status(
        _Cursor(db),
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        lock_task=True,
        initiating_selection_request_id="initiating-request",
    )

    assert status.startAllowed is True
    assert status.state == "not_committed"


def test_write_boundary_context_keeps_same_initiating_system_blocker_visible() -> None:
    db = _Db(revision=14)
    db.jobs["initiating-request"] = _blocker_job(
        "initiating-request",
        14,
        "same_request_system_blocker",
    )

    status = evaluate_current_execution_start_status(
        _Cursor(db),
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        lock_task=True,
        initiating_selection_request_id="initiating-request",
    )

    assert status.startAllowed is False
    assert status.state == "blocked"
    assert status.blockingReasonCode == "same_request_system_blocker"
    assert status.responsibility == "SYSTEM"


def test_write_boundary_context_keeps_different_request_system_blocker_visible() -> None:
    db = _Db(revision=15)
    db.jobs["initiating-request"] = _active_job(
        "initiating-request",
        15,
        "leased",
    )
    db.jobs["competing-request"] = _blocker_job(
        "competing-request",
        15,
        "different_request_system_blocker",
        sequence=2,
    )

    status = evaluate_current_execution_start_status(
        _Cursor(db),
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        lock_task=True,
        initiating_selection_request_id="initiating-request",
    )

    assert status.startAllowed is False
    assert status.blockingReasonCode == "different_request_system_blocker"
    assert status.responsibility == "SYSTEM"


def test_evaluator_without_write_boundary_context_keeps_original_current_job_behavior() -> None:
    db = _Db(revision=16)
    db.jobs["request-a"] = _active_job("request-a", 16, "queued")

    status = evaluate_current_execution_start_status(
        _Cursor(db),
        task_id="task-a",
        owner_id="owner",
        workspace_id="workspace",
        lock_task=False,
    )

    assert status.startAllowed is False
    assert status.state == "accepted"
    assert status.responsibility == "NONE"

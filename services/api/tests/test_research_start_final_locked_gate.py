from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.modules.research_execution_start_status import (
    ResearchExecutionInitialStartDenied,
)
from app.modules.research_orchestration_repository import ResearchOrchestrationRepository
from tests.support.research_lifecycle import build_confirmed_research_task


class _Cursor:
    def __init__(self, *, replay=None, start_row=None) -> None:
        self.replay = replay
        self.start_row = start_row
        self._row = None
        self.sql: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement: str, params=()) -> None:
        normalized = " ".join(statement.split())
        self.sql.append(normalized)
        if normalized.startswith(
            "SELECT command_hash, payload_hash, execution_decision_id"
        ):
            self._row = self.replay
        elif normalized.startswith("SELECT command_payload FROM openair_model_jobs"):
            self._row = {"command_payload": {"requestKind": "explicit_start"}}
        elif normalized.startswith("SELECT task.payload, task.revision"):
            self._row = self.start_row
        else:
            raise AssertionError(f"unexpected SQL before Start Gate: {normalized}")

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _Uow:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    @contextmanager
    def transaction(self):
        yield _Connection(self._cursor)


class _Repo(ResearchOrchestrationRepository):
    def __init__(self, *, task, cursor: _Cursor) -> None:
        self._task = task
        self._uow = _Uow(cursor)

    def _require_write_enabled(self) -> None:
        return None

    def _require_postgres(self) -> None:
        return None

    def _require_expected_revision(self, revision: int) -> None:
        return None

    def _load_locked_task(self, cursor, *, task_id, owner_id, workspace_id):
        return self._task, {}

    def _apply_pending_execution_policy_at_boundary(
        self, cursor, *, task, owner_id, workspace_id, changed_by
    ):
        return task, {}


def _prepared(task):
    return SimpleNamespace(
        task=task,
        owner_id="owner",
        workspace_id="workspace",
        selection_request_id="request-a",
        selection_command_hash="sha256:command",
        expected_task_revision=task.revision,
        final_tie_candidate_ids=("candidate-a",),
        fairness=None,
    )


def test_final_selection_write_rechecks_shared_start_gate_under_task_lock() -> None:
    task = build_confirmed_research_task()
    payload = task.model_dump(mode="json", by_alias=True)
    start_row = {
        "payload": payload,
        "revision": task.revision,
        "current_job_state": "blocked",
        "current_job_expected_revision": task.revision,
        "current_job_result_payload": {
            "reasonCode": "future_system_integrity_reason",
            "facts": {"taskRevision": task.revision},
        },
        "current_job_last_error_code": None,
    }
    cursor = _Cursor(start_row=start_row)
    reached_materialization = False

    def freeze(_task, _winner):
        nonlocal reached_materialization
        reached_materialization = True
        raise AssertionError("Start Gate must run before materialization")

    with pytest.raises(ResearchExecutionInitialStartDenied) as exc_info:
        _Repo(task=task, cursor=cursor).commit_prepared_selection(
            _prepared(task), freeze
        )

    assert reached_materialization is False
    assert exc_info.value.start_status.blockingReasonCode == (
        "future_system_integrity_reason"
    )
    assert exc_info.value.start_status.responsibility == "SYSTEM"
    assert any("FOR UPDATE OF task" in statement for statement in cursor.sql)


def test_same_request_replay_returns_before_final_start_gate() -> None:
    decision = SimpleNamespace(execution_decision_id="decision-a")
    task = SimpleNamespace(
        task_id="task-a",
        revision=10,
        registry_snapshot_ref="snapshot",
        active_research_design_ref="design",
        execution_decisions=[decision],
    )
    cursor = _Cursor(
        replay={
            "command_hash": "sha256:command",
            "payload_hash": "sha256:payload",
            "execution_decision_id": "decision-a",
        }
    )

    result = _Repo(task=task, cursor=cursor).commit_prepared_selection(
        _prepared(task), lambda *_args: None
    )

    assert result.replayed is True
    assert result.decision is decision
    assert not any(
        statement.startswith("SELECT command_payload FROM openair_model_jobs")
        for statement in cursor.sql
    )

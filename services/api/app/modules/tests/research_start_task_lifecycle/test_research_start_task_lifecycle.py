from __future__ import annotations

import json
import re
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from app.modules.research_task_lifecycle import (
    ArtifactLifecycleFact,
    RecoveryLifecycleFact,
    ResearchDesignLifecycleFact,
    ResearchStartLifecycleFact,
    UserTaskLifecycleFact,
    reconcile_research_start_task_lifecycle,
    reconcile_task_lifecycle,
)
from app.modules.research_task_lifecycle_repository import (
    apply_research_start_lifecycle_fact_locked,
    commit_reconciled_task_projection_locked,
    commit_task_lifecycle_mutation_locked,
)
from app.modules.task_tombstone_repository import commit_task_tombstone_locked


@pytest.mark.parametrize(
    ("fact", "task", "expected_status", "expected_progress"),
    [
        (
            ResearchStartLifecycleFact.PLANNING_FAILED,
            {"status": "draft"},
            "failed",
            "failed",
        ),
        (
            ResearchStartLifecycleFact.EXTERNAL_RESULT_UNKNOWN,
            {"status": "draft"},
            "awaiting_confirmation",
            "external_result_unknown",
        ),
        (
            ResearchStartLifecycleFact.RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED,
            {
                "status": "awaiting_confirmation",
                "progressProjection": {"taskProgress": {"status": "external_result_unknown"}},
            },
            "draft",
            "draft",
        ),
        (
            ResearchStartLifecycleFact.PLAN_REGENERATION_AUTHORIZED,
            {"status": "failed"},
            "draft",
            "draft",
        ),
        (
            ResearchStartLifecycleFact.PLAN_REGENERATION_AUTHORIZED,
            {"status": "draft"},
            "draft",
            "draft",
        ),
    ],
)
def test_research_start_facts_have_one_deterministic_task_conclusion(
    fact: ResearchStartLifecycleFact,
    task: dict[str, Any],
    expected_status: str,
    expected_progress: str,
) -> None:
    transition = reconcile_research_start_task_lifecycle(task, fact=fact)

    assert transition is not None
    assert transition.task_status == expected_status
    assert transition.progress_status == expected_progress


@pytest.mark.parametrize(
    ("fact", "task"),
    [
        (ResearchStartLifecycleFact.PLANNING_FAILED, {"status": "running"}),
        (ResearchStartLifecycleFact.PLAN_REGENERATION_AUTHORIZED, {"status": "running"}),
        (ResearchStartLifecycleFact.PLAN_REGENERATION_AUTHORIZED, {"status": "completed"}),
        (ResearchStartLifecycleFact.EXTERNAL_RESULT_UNKNOWN, {"status": "completed"}),
        (ResearchStartLifecycleFact.RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED, {"status": "failed"}),
        (
            ResearchStartLifecycleFact.RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED,
            {
                "status": "awaiting_confirmation",
                "progressProjection": {"taskProgress": {"status": "needs_input"}},
            },
        ),
    ],
)
def test_late_or_unrelated_research_start_facts_do_not_rewrite_task(
    fact: ResearchStartLifecycleFact,
    task: dict[str, Any],
) -> None:
    assert reconcile_research_start_task_lifecycle(task, fact=fact) is None


def test_confirmed_legacy_research_start_plan_uses_the_lifecycle_convergence() -> None:
    transition = reconcile_task_lifecycle(
        {"status": "draft", "creationStatus": "awaiting_confirmation"},
        fact=ResearchStartLifecycleFact.PLAN_CONFIRMED,
    )

    assert transition is not None
    assert transition.task_status == "not_started"
    assert transition.progress_status == "not_started"
    assert transition.expected_database_status == "draft"


@pytest.mark.parametrize(
    "task",
    [
        {"status": "draft", "creationStatus": "revision_requested"},
        {"status": "running", "creationStatus": "awaiting_confirmation"},
    ],
)
def test_plan_confirmation_rejects_a_non_confirmable_task(task: dict[str, str]) -> None:
    assert reconcile_task_lifecycle(task, fact=ResearchStartLifecycleFact.PLAN_CONFIRMED) is None


@pytest.mark.parametrize("status", ["draft", "cancelled"])
def test_draft_cancellation_uses_the_lifecycle_convergence(status: str) -> None:
    transition = reconcile_task_lifecycle(
        {"status": status},
        fact=UserTaskLifecycleFact.DRAFT_CANCELLED,
    )

    assert transition is not None
    assert transition.task_status == "cancelled"
    assert transition.expected_database_status == status


@pytest.mark.parametrize("status", ["not_started", "running", "completed"])
def test_draft_cancellation_cannot_overwrite_another_lifecycle_state(status: str) -> None:
    assert (
        reconcile_task_lifecycle(
            {"status": status},
            fact=UserTaskLifecycleFact.DRAFT_CANCELLED,
        )
        is None
    )


@pytest.mark.parametrize(
    ("fact", "task", "expected_status"),
    [
        (
            ResearchDesignLifecycleFact.DESIGN_APPROVED,
            {"status": "draft"},
            "not_started",
        ),
        (
            ResearchDesignLifecycleFact.DESIGN_APPROVED,
            {"status": "awaiting_confirmation", "startedAt": "2026-10-07T00:00:00Z"},
            "running",
        ),
        (
            ResearchDesignLifecycleFact.SEMANTIC_MATERIAL_GAP_RECOVERY_DRAFTED,
            {"status": "running"},
            "awaiting_confirmation",
        ),
    ],
)
def test_research_design_facts_use_the_same_task_lifecycle_convergence(
    fact: ResearchDesignLifecycleFact,
    task: dict[str, Any],
    expected_status: str,
) -> None:
    transition = reconcile_task_lifecycle(task, fact=fact)

    assert transition is not None
    assert transition.task_status == expected_status


def test_losing_the_last_usable_research_output_requires_confirmation() -> None:
    transition = reconcile_task_lifecycle(
        {"status": "completed"},
        fact=ArtifactLifecycleFact.ALL_RESEARCH_OUTPUTS_NO_LONGER_USABLE,
    )

    assert transition is not None
    assert transition.task_status == "awaiting_confirmation"
    assert transition.progress_status == "awaiting_confirmation"
    assert transition.expected_database_status == "completed"


def test_losing_an_output_cannot_rewrite_a_task_that_is_not_completed() -> None:
    assert (
        reconcile_task_lifecycle(
            {"status": "running"},
            fact=ArtifactLifecycleFact.ALL_RESEARCH_OUTPUTS_NO_LONGER_USABLE,
        )
        is None
    )


@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("failed", "running"),
        ("awaiting_confirmation", "running"),
        ("running", "running"),
    ],
)
def test_confirmed_recovery_successor_uses_the_same_lifecycle_convergence(
    status: str,
    expected_status: str,
) -> None:
    transition = reconcile_task_lifecycle(
        {"status": status},
        fact=RecoveryLifecycleFact.SUCCESSOR_EXECUTION_CONFIRMED,
    )

    assert transition is not None
    assert transition.task_status == expected_status
    assert transition.progress_status == "running"
    assert transition.expected_database_status == status


@pytest.mark.parametrize("status", ["completed", "cancelled", "draft"])
def test_recovery_successor_cannot_resurrect_a_terminal_or_unstarted_task(status: str) -> None:
    assert (
        reconcile_task_lifecycle(
            {"status": status},
            fact=RecoveryLifecycleFact.SUCCESSOR_EXECUTION_CONFIRMED,
        )
        is None
    )


class _Cursor:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.current_sql = ""
        self.calls: list[tuple[str, object]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: object = None) -> None:
        self.current_sql = sql
        self.calls.append((sql, params))
        self.rowcount = 1 if "UPDATE openair_tasks" in sql else 0

    def fetchone(self) -> dict[str, Any] | None:
        if "SELECT payload, revision FROM openair_tasks" not in self.current_sql:
            return None
        return {"revision": 4, "payload": self._payload}


class _TaskMutationCursor:
    def __init__(self, task: dict[str, Any]) -> None:
        self.task = deepcopy(task)
        self.current_sql = ""
        self.rowcount = 0
        self._next: dict[str, Any] | None = None

    def __enter__(self) -> _TaskMutationCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.current_sql = sql
        self.rowcount = 0
        self._next = None
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT payload, revision, status, current_stage, title, domain"):
            self._next = {
                "payload": deepcopy(self.task),
                "revision": self.task["revision"],
                "status": self.task["status"],
                "current_stage": self.task["currentStage"],
                "title": self.task["title"],
                "domain": self.task.get("domain"),
            }
        elif normalized.startswith("UPDATE openair_tasks SET status"):
            assert isinstance(params, tuple)
            self.task = json.loads(params[5])
            self.rowcount = 1

    def fetchone(self) -> dict[str, Any] | None:
        row = self._next
        self._next = None
        return row


class _TaskMutationConnection:
    def __init__(self, cursor: _TaskMutationCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _TaskMutationCursor:
        return self._cursor


def _task_mutation_repository(cursor: _TaskMutationCursor) -> Any:
    from app.modules.task_repository import TaskRepository

    class _Repository(TaskRepository):
        @contextmanager
        def _connect(self) -> Any:
            yield _TaskMutationConnection(cursor)

    return _Repository()


def test_lifecycle_writer_advances_task_and_projection_in_one_revision() -> None:
    cursor = _Cursor(
        {
            "taskId": "task_reserved",
            "revision": 4,
            "status": "draft",
            "progressProjection": {"revision": 4, "taskProgress": {"status": "draft"}},
        }
    )

    changed = apply_research_start_lifecycle_fact_locked(
        cursor,
        session={
            "reserved_task_id": "task_reserved",
            "owner_id": "owner",
            "workspace_id": "workspace",
        },
        fact=ResearchStartLifecycleFact.PLANNING_FAILED,
    )

    update_sql, update = next(
        (sql, params) for sql, params in cursor.calls if "UPDATE openair_tasks" in sql
    )
    assert changed is True
    assert isinstance(update, tuple)
    assert "AND revision = %s AND status = %s" in update_sql
    assert update[:2] == ("failed", 5)
    assert update[-1] == "draft"
    persisted = json.loads(update[2])
    assert persisted["revision"] == 5
    assert persisted["status"] == "failed"
    assert persisted["progressProjection"]["revision"] == 5
    assert persisted["progressProjection"]["taskProgress"]["status"] == "failed"


def test_retry_restoration_requires_unknown_confirmation_state_and_advances_one_revision() -> None:
    cursor = _Cursor(
        {
            "taskId": "task_reserved",
            "revision": 4,
            "status": "awaiting_confirmation",
            "progressProjection": {
                "revision": 4,
                "taskProgress": {"status": "external_result_unknown"},
            },
        }
    )

    changed = apply_research_start_lifecycle_fact_locked(
        cursor,
        session={
            "reserved_task_id": "task_reserved",
            "owner_id": "owner",
            "workspace_id": "workspace",
        },
        fact=ResearchStartLifecycleFact.RETRY_AUTHORIZED_AFTER_NOT_SUBMITTED,
    )

    update_sql, update = next(
        (sql, params) for sql, params in cursor.calls if "UPDATE openair_tasks" in sql
    )
    assert changed is True
    assert isinstance(update, tuple)
    assert "AND revision = %s AND status = %s" in update_sql
    assert update[:2] == ("draft", 5)
    assert update[-1] == "awaiting_confirmation"
    persisted = json.loads(update[2])
    assert persisted["revision"] == 5
    assert persisted["status"] == "draft"
    assert persisted["progressProjection"]["revision"] == 5
    assert persisted["progressProjection"]["taskProgress"]["status"] == "draft"


def test_common_lifecycle_writer_uses_locked_database_status_over_stale_payload() -> None:
    cursor = _Cursor({})
    payload = {
        "taskId": "task_completed",
        "revision": 4,
        # A stale cached payload must not keep this Task in draft after the
        # locked database row proves that it already completed.
        "status": "draft",
        "progressProjection": {"revision": 4, "taskProgress": {"status": "draft"}},
    }

    changed = commit_task_lifecycle_mutation_locked(
        cursor,
        task_row={"revision": 4, "status": "completed"},
        payload=payload,
        task_id="task_completed",
        owner_id="owner",
        workspace_id="workspace",
        fact=ArtifactLifecycleFact.ALL_RESEARCH_OUTPUTS_NO_LONGER_USABLE,
    )

    _sql, update = next(
        (sql, params) for sql, params in cursor.calls if "UPDATE openair_tasks" in sql
    )
    assert changed is True
    assert isinstance(update, tuple)
    assert update[:2] == ("awaiting_confirmation", 5)
    persisted = json.loads(update[2])
    assert persisted["status"] == "awaiting_confirmation"
    assert persisted["progressProjection"]["taskProgress"]["status"] == "awaiting_confirmation"


def test_runtime_reconciliation_uses_the_same_task_write_boundary() -> None:
    cursor = _Cursor({})
    payload = {
        "taskId": "task_runtime",
        "revision": 4,
        "status": "running",
        "progressProjection": {"revision": 4, "taskProgress": {"status": "running"}},
    }

    changed = commit_reconciled_task_projection_locked(
        cursor,
        task_row={"revision": 4, "status": "running"},
        payload=payload,
        task_id="task_runtime",
        owner_id="owner",
        workspace_id="workspace",
        reconciled_status="awaiting_confirmation",
        reconciled_progress_status="awaiting_confirmation",
    )

    update_sql, update = next(
        (sql, params) for sql, params in cursor.calls if "UPDATE openair_tasks" in sql
    )
    assert changed is True
    assert isinstance(update, tuple)
    assert "AND revision = %s AND status = %s" in update_sql
    assert update[:2] == ("awaiting_confirmation", 5)
    assert update[-1] == "running"
    persisted = json.loads(update[2])
    assert persisted["status"] == "awaiting_confirmation"
    assert persisted["revision"] == 5


def test_tombstone_is_not_a_normal_task_lifecycle_transition() -> None:
    cursor = _Cursor({})
    commit_task_tombstone_locked(
        cursor,
        task_row={
            "revision": 4,
            "payload": {
                "taskId": "task_deleted",
                "status": "running",
                "artifacts": [{"artifactId": "artifact_old"}],
                "capabilityCalls": [{"capabilityCallId": "call_old"}],
            },
        },
        task_id="task_deleted",
        owner_id="owner",
        workspace_id="workspace",
        deleted_at="2026-10-08T00:00:00+00:00",
    )

    update_sql, update = next(
        (sql, params) for sql, params in cursor.calls if "UPDATE openair_tasks" in sql
    )
    assert isinstance(update, tuple)
    assert update[:2] == ("deleted", 5)
    assert update[-1] == 4
    assert "status <> %s" in update_sql
    tombstone = json.loads(update[2])
    assert tombstone["status"] == "deleted"
    assert tombstone["artifacts"] == []
    assert tombstone["capabilityCalls"] == []


def test_task_status_sql_is_limited_to_lifecycle_and_tombstone_boundaries() -> None:
    direct_status_write = re.compile(r"UPDATE openair_tasks\s+SET status", re.DOTALL)
    modules_dir = Path(__file__).resolve().parents[2]
    lifecycle_source = (modules_dir / "research_task_lifecycle_repository.py").read_text(
        encoding="utf-8"
    )
    tombstone_source = (modules_dir / "task_tombstone_repository.py").read_text(encoding="utf-8")
    task_repository_source = (modules_dir / "task_repository.py").read_text(encoding="utf-8")
    assert "UPDATE openair_tasks" in lifecycle_source
    assert 'column_assignments = ["status = %s"]' in lifecycle_source
    assert direct_status_write.search(tombstone_source)
    for name in ("collaboration_repository.py", "research_orchestration_repository.py"):
        assert direct_status_write.search((modules_dir / name).read_text(encoding="utf-8")) is None
    assert direct_status_write.search(task_repository_source) is None

    start = task_repository_source.index("    def soft_delete_task(")
    end = task_repository_source.index("\n    def delete_task(", start)
    assert "UPDATE openair_tasks" not in task_repository_source[start:end]


def test_task_repository_confirm_plan_uses_the_lifecycle_boundary() -> None:
    cursor = _TaskMutationCursor(
        {
            "taskId": "task_draft",
            "ownerId": "owner",
            "workspaceId": "workspace",
            "revision": 4,
            "status": "draft",
            "creationStatus": "awaiting_confirmation",
            "currentStage": "problem_modeling",
            "title": "Draft task",
            "domain": None,
            "progressProjection": {"revision": 4, "taskProgress": {"status": "draft"}},
        }
    )
    candidate = deepcopy(cursor.task)
    candidate.update({"status": "not_started", "creationStatus": "confirmed"})

    updated = _task_mutation_repository(cursor).confirm_research_start_plan(
        task=candidate,
        owner_id="owner",
        workspace_id="workspace",
    )

    assert updated["status"] == "not_started"
    assert updated["revision"] == 5
    assert cursor.task["status"] == "not_started"
    assert cursor.task["revision"] == 5


def test_task_repository_cancel_draft_uses_the_lifecycle_boundary() -> None:
    cursor = _TaskMutationCursor(
        {
            "taskId": "task_draft",
            "ownerId": "owner",
            "workspaceId": "workspace",
            "revision": 4,
            "status": "draft",
            "creationStatus": "awaiting_confirmation",
            "currentStage": "problem_modeling",
            "title": "Draft task",
            "domain": None,
            "progressProjection": {"revision": 4, "taskProgress": {"status": "draft"}},
        }
    )

    updated = _task_mutation_repository(cursor).cancel_draft_scoped(
        task_id="task_draft",
        owner_id="owner",
        workspace_id="workspace",
    )

    assert updated["status"] == "cancelled"
    assert updated["creationStatus"] == "cancelled"
    assert updated["revision"] == 5
    assert cursor.task["status"] == "cancelled"


def test_postgres_taskstore_refuses_legacy_status_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings
    from app.modules.task_store import TaskStore, TaskStorePostgresMutationForbidden

    monkeypatch.setattr(settings, "persistence_backend", "postgres")

    with pytest.raises(TaskStorePostgresMutationForbidden):
        TaskStore().patch_status("task_draft", "running")

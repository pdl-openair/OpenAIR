from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import os
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import settings
from app.main import app
from app.modules import task_repository
from app.modules.task_repository import TASKS_TABLE_SQL, TaskRepository
from app.modules.task_store import store


client = TestClient(app)
DSN_ENV = "OPENAIR_TASK_REPOSITORY_TEST_DATABASE_URL"
STAGES = ["problem_modeling", "solution_design", "execution_validation", "insight_generation"]
EXECUTABLE_SUBSTAGE_BY_STAGE = [
    "question_refinement",
    "variable_definition",
    "evidence_extraction",
    "report_generation",
]


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        pytest.fail(f"{DSN_ENV} is required for real PostgreSQL evidence")
    database = urlparse(dsn).path.lstrip("/").lower()
    if "test" not in database and "qa" not in database:
        pytest.fail(f"{DSN_ENV} must identify an isolated test or qa database")
    return dsn


def _editable(revision: int = 4, *, goal_summary: str = "PostgreSQL CAS draft") -> dict:
    return {
        "expectedRevision": revision,
        "goalSummary": goal_summary,
        "understanding": {"evidenceBoundaries": ["provided evidence only"]},
        "clarifications": [],
        "stages": [
            {
                "stageKey": stage,
                "title": f"Title {index}",
                "description": f"Description {index}",
                "substages": [{
                    "substage": EXECUTABLE_SUBSTAGE_BY_STAGE[index],
                    "label": f"Step {index}",
                    "action": f"Action {index}",
                    "expectedArtifact": f"Artifact {index}",
                }],
            }
            for index, stage in enumerate(STAGES)
        ],
    }


def _task() -> dict:
    return {
        "taskId": "task_pg_plan",
        "ownerId": "owner-a",
        "workspaceId": "workspace-a",
        "createdBy": "owner-a",
        "title": "draft",
        "goalSummary": "old",
        "domain": "research_task",
        "skillPack": "sdr",
        "currentStage": "problem_modeling",
        "status": "draft",
        "creationStatus": "awaiting_confirmation",
        "creationPlan": {
            "goalSummary": "old",
            "domain": "research_task",
            "planningAudit": {"keep": "yes"},
            "clarifications": [],
            "stages": [],
            "readyToConfirm": False,
        },
        "stages": STAGES,
        "stageDetails": [],
        "stageCatalog": [],
        "artifacts": [],
        "materials": [],
        "memoryRecords": [],
        "confirmations": [],
        "capabilityCalls": [],
        "revision": 4,
    }


class PostgresTaskRepository(TaskRepository):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextmanager
    def _connect(self):
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            yield connection
            connection.commit()


@pytest.fixture()
def database():
    base_dsn = _dsn()
    schema = f"task_repository_{uuid4().hex}"
    with psycopg.connect(base_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = make_conninfo(base_dsn, options=f"-csearch_path={schema}")
    try:
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
            connection.execute(TASKS_TABLE_SQL)
            connection.execute(
                "ALTER TABLE openair_tasks "
                "ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0"
            )
            assert connection.execute("SELECT current_schema() AS name").fetchone()["name"] == schema
        yield dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _insert_task(dsn: str, task: dict) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """INSERT INTO openair_tasks (
                   task_id, owner_id, workspace_id, status, current_stage, title,
                   domain, created_at, updated_at, payload, revision
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s, %s)""",
            (
                task["taskId"], task["ownerId"], task["workspaceId"], task["status"],
                task["currentStage"], task["title"], task["domain"], Jsonb(task), task["revision"],
            ),
        )


def _read_task(dsn: str) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT title, domain, revision, payload FROM openair_tasks WHERE task_id = 'task_pg_plan'"
        ).fetchone()
    assert row is not None
    return dict(row)


def _install_deterministic_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        task_repository,
        "prepare_task_plan_candidate",
        lambda task, editable, **_kwargs: task_repository.build_task_plan_projection(
            task, editable, updated_at="2026-07-24T00:00:00Z"
        ),
    )
    monkeypatch.setattr(task_repository, "precompile_creation_plan", lambda **_kwargs: None)


def test_real_postgres_scoped_revision_write_persists(
    database: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task()
    _insert_task(database, task)
    _install_deterministic_candidate(monkeypatch)

    result = PostgresTaskRepository(database).update_task_plan(
        task=task,
        editable=_editable(),
        owner_id="owner-a",
        workspace_id="workspace-a",
    )

    persisted = _read_task(database)
    assert result["revision"] == 5
    assert persisted["revision"] == 5
    assert persisted["title"] == result["title"] == "draft"
    assert persisted["payload"] == result
    assert persisted["payload"]["goalSummary"] == "PostgreSQL CAS draft"
    assert persisted["payload"]["creationPlan"]["planningAudit"] == {"keep": "yes"}


def test_real_postgres_losing_cas_does_not_overwrite_committed_value(
    database: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _task()
    _insert_task(database, stale)
    _install_deterministic_candidate(monkeypatch)
    repository = PostgresTaskRepository(database)
    winner = repository.update_task_plan(
        task=deepcopy(stale),
        editable=_editable(goal_summary="winning value"),
        owner_id="owner-a",
        workspace_id="workspace-a",
    )

    with pytest.raises(RuntimeError, match="task_revision_conflict"):
        repository.update_task_plan(
            task=deepcopy(stale),
            editable=_editable(goal_summary="losing stale value"),
            owner_id="owner-a",
            workspace_id="workspace-a",
        )

    persisted = _read_task(database)
    assert persisted["revision"] == 5
    assert persisted["payload"] == winner
    assert persisted["payload"]["goalSummary"] == "winning value"


def test_real_postgres_final_validation_failure_rolls_back_every_column(
    database: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _task()
    _insert_task(database, original)
    _install_deterministic_candidate(monkeypatch)
    monkeypatch.setattr(
        task_repository,
        "precompile_creation_plan",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("final precompile failed")),
    )

    with pytest.raises(RuntimeError, match="final precompile failed"):
        PostgresTaskRepository(database).update_task_plan(
            task=original,
            editable=_editable(goal_summary="must roll back"),
            owner_id="owner-a",
            workspace_id="workspace-a",
        )

    persisted = _read_task(database)
    assert persisted["revision"] == 4
    assert persisted["title"] == "draft"
    assert persisted["domain"] == "research_task"
    assert persisted["payload"] == original


def test_postgres_route_maps_repository_cas_conflict_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task()
    original_backend = settings.persistence_backend
    monkeypatch.setattr(settings, "persistence_backend", "postgres")
    monkeypatch.setattr(store, "get_task", lambda _task_id: deepcopy(task))
    monkeypatch.setattr(
        TaskRepository,
        "update_task_plan",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("task_revision_conflict")),
    )
    try:
        response = client.post(
            "/api/tasks/task_pg_plan/update-plan",
            headers={"X-OpenAIR-Owner-Id": "owner-a", "X-OpenAIR-Workspace-Id": "workspace-a"},
            json=_editable(),
        )
    finally:
        settings.persistence_backend = original_backend

    assert response.status_code == 409
    assert response.json()["detail"] == "task_revision_conflict"

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.model_job_queue import ModelJobQueueRepository
from tests.support.research_lifecycle import build_confirmed_research_task


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


class _ReconciliationCursor:
    def __init__(self, rows=None, *, start_jobs=None) -> None:
        self.sql = ""
        self.rows = list(rows or [])
        self.start_jobs = dict(start_jobs or {})
        self.deterministic_jobs: set[str] = set()
        self.scan_queries = 0
        self._many = []
        self._one = None

    def execute(self, sql, params) -> None:
        self.sql = sql
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT task.task_id, task.owner_id"):
            self.scan_queries += 1
            after = (
                None
                if params[0] is None
                else (params[0], str(params[2]), str(params[3]), str(params[4]))
            )
            candidates = [
                row
                for row in sorted(self.rows, key=_recovery_key)
                if row["task_id"] not in self.deterministic_jobs
                and (after is None or _recovery_key(row) > after)
            ]
            self._many = candidates[: int(params[-1])]
            self._one = None
            return
        if normalized.startswith("SELECT task.payload, task.revision"):
            task_id = str(params[0])
            row = next(item for item in self.rows if item["task_id"] == task_id)
            job = self.start_jobs.get(task_id)
            self._one = {
                "payload": row["payload"],
                "revision": row["revision"],
                "current_job_state": job.get("state") if job else None,
                "current_job_expected_revision": (
                    job.get("expected_revision") if job else None
                ),
                "current_job_result_payload": (
                    job.get("result_payload") if job else None
                ),
                "current_job_last_error_code": (
                    job.get("last_error_code") if job else None
                ),
            }
            self._many = []
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchall(self):
        return self._many

    def fetchone(self):
        return self._one


class _ReconciliationConnection:
    def __init__(self, cursor: _ReconciliationCursor) -> None:
        self._cursor = cursor

    @contextmanager
    def cursor(self):
        yield self._cursor


class _ReconciliationRepository(ModelJobQueueRepository):
    def __init__(self, connection: _ReconciliationConnection) -> None:
        self._connection = connection

    @contextmanager
    def _connect(self):
        yield self._connection


def _recovery_key(row: dict) -> tuple:
    return (
        row["updated_at"],
        str(row["task_id"]),
        str(row["owner_id"]),
        str(row["workspace_id"]),
    )


def _candidate(task_id: str, sequence: int, *, status: str = "not_started") -> dict:
    task = build_confirmed_research_task()
    payload = task.model_dump(mode="json", by_alias=True)

    def retarget(value):
        if isinstance(value, dict):
            if "taskId" in value:
                value["taskId"] = task_id
            for item in value.values():
                retarget(item)
        elif isinstance(value, list):
            for item in value:
                retarget(item)

    retarget(payload)
    payload["status"] = status
    return {
        "task_id": task_id,
        "owner_id": "owner",
        "workspace_id": "workspace",
        "revision": task.revision,
        "payload": payload,
        "updated_at": datetime(2026, 9, 20, tzinfo=timezone.utc)
        + timedelta(seconds=sequence),
    }


def _blocked_start_job(row: dict) -> dict:
    return {
        "state": "blocked",
        "expected_revision": row["revision"],
        "result_payload": {
            "reasonCode": "permanent_integrity_blocker",
            "facts": {"taskRevision": row["revision"]},
        },
        "last_error_code": "permanent_integrity_blocker",
    }


def _job(kind: str, job_id: str = "request-1") -> dict:
    return {
        "job_kind": kind,
        "resource_id": "resource-1",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "job_id": job_id,
    }


def test_execution_selection_recovers_only_from_matching_durable_decision() -> None:
    cursor = _Cursor(
        {
            "revision": 7,
            "payload": {
                "executionDecisions": [
                    {
                        "selectionRequestId": "request-1",
                        "executionDecisionId": "decision-1",
                    }
                ]
            },
        }
    )
    assert ModelJobQueueRepository._reconciled_business_result(
        cursor, _job("execution_selection")
    ) == {
        "executionDecisionId": "decision-1",
        "taskRevision": 7,
        "reconciledFromDurableState": True,
    }


def test_confirmation_reconciliation_depends_on_current_design_not_workflow_admission() -> None:
    cursor = _ReconciliationCursor()
    repository = _ReconciliationRepository(_ReconciliationConnection(cursor))

    assert repository.reconcile_confirmed_design_jobs() == 0
    assert "task.payload ? 'workflowAdmission'" not in cursor.sql
    assert "task.payload ? 'activeResearchDesignRef'" in cursor.sql
    assert "task.payload ? 'researchDesignVersions'" in cursor.sql
    assert "research-design:auto-start:" in cursor.sql


def test_confirmation_reconciliation_restores_the_same_deterministic_start_intent() -> None:
    task = build_confirmed_research_task()
    cursor = _ReconciliationCursor([_candidate(task.task_id, 1)])

    class Repository(_ReconciliationRepository):
        def __init__(self):
            super().__init__(_ReconciliationConnection(cursor))
            self.enqueued = []

        def enqueue(self, **kwargs):
            self.enqueued.append(kwargs)
            cursor.deterministic_jobs.add(kwargs["resource_id"])
            return {"state": "queued"}

    repository = Repository()
    assert repository.reconcile_confirmed_design_jobs() == 1
    active = task.active_research_design_ref
    assert active is not None
    assert repository.enqueued == [{
        "resource_kind": "task",
        "resource_id": task.task_id,
        "owner_id": "owner",
        "workspace_id": "workspace",
        "job_id": (
            f"research-design:auto-start:{active.research_design_id}:v{active.version}"
        ),
        "job_kind": "execution_selection",
        "expected_revision": task.revision,
        "command_payload": {"requestKind": "confirmed_design"},
    }]


def test_confirmation_reconciliation_scans_past_sixteen_permanent_blockers() -> None:
    blocked = [_candidate(f"blocked-{index:02d}", index) for index in range(16)]
    recoverable = _candidate("recoverable-17", 17)
    cursor = _ReconciliationCursor(
        [*blocked, recoverable],
        start_jobs={row["task_id"]: _blocked_start_job(row) for row in blocked},
    )

    class Repository(_ReconciliationRepository):
        def __init__(self):
            super().__init__(_ReconciliationConnection(cursor))
            self.enqueued = []

        def enqueue(self, **kwargs):
            self.enqueued.append(kwargs)
            cursor.deterministic_jobs.add(kwargs["resource_id"])
            return {"state": "queued"}

    repository = Repository()

    assert repository.reconcile_confirmed_design_jobs(limit=16) == 1
    assert [row["resource_id"] for row in repository.enqueued] == ["recoverable-17"]
    assert cursor.scan_queries == 2


def test_confirmation_reconciliation_skips_started_and_committed_tasks() -> None:
    started = _candidate("already-started", 1, status="running")
    committed = _candidate("already-committed", 2)
    cursor = _ReconciliationCursor(
        [started, committed],
        start_jobs={
            "already-committed": {
                "state": "succeeded",
                "expected_revision": committed["revision"],
                "result_payload": {"executionDecisionId": "decision-committed"},
                "last_error_code": None,
            }
        },
    )

    class Repository(_ReconciliationRepository):
        def __init__(self):
            super().__init__(_ReconciliationConnection(cursor))
            self.enqueued = []

        def enqueue(self, **kwargs):
            self.enqueued.append(kwargs)
            return {"state": "queued"}

    repository = Repository()

    assert repository.reconcile_confirmed_design_jobs() == 0
    assert repository.enqueued == []


def test_confirmation_reconciliation_does_not_requeue_permanent_blocker() -> None:
    blocked = _candidate("permanently-blocked", 1)
    cursor = _ReconciliationCursor(
        [blocked],
        start_jobs={"permanently-blocked": _blocked_start_job(blocked)},
    )

    class Repository(_ReconciliationRepository):
        def __init__(self):
            super().__init__(_ReconciliationConnection(cursor))
            self.enqueued = []

        def enqueue(self, **kwargs):
            self.enqueued.append(kwargs)
            return {"state": "queued"}

    repository = Repository()

    assert repository.reconcile_confirmed_design_jobs() == 0
    assert repository.reconcile_confirmed_design_jobs() == 0
    assert repository.enqueued == []


def test_confirmation_reconciliation_skips_noncurrent_confirmed_design() -> None:
    stale = _candidate("stale-confirmed-design", 1)
    current_draft = deepcopy(stale["payload"]["researchDesignVersions"][0])
    current_draft.update({
        "version": 2,
        "supersedesVersion": 1,
        "status": "draft",
        "confirmedAt": None,
        "confirmationRef": None,
    })
    stale["payload"]["researchDesignVersions"].append(current_draft)
    cursor = _ReconciliationCursor([stale])

    class Repository(_ReconciliationRepository):
        def __init__(self):
            super().__init__(_ReconciliationConnection(cursor))
            self.enqueued = []

        def enqueue(self, **kwargs):
            self.enqueued.append(kwargs)
            return {"state": "queued"}

    repository = Repository()

    assert repository.reconcile_confirmed_design_jobs() == 0
    assert repository.enqueued == []


def test_confirmation_reconciliation_is_idempotent_across_repeated_scans() -> None:
    recoverable = _candidate("recover-once", 1)
    cursor = _ReconciliationCursor([recoverable])

    class Repository(_ReconciliationRepository):
        def __init__(self):
            super().__init__(_ReconciliationConnection(cursor))
            self.enqueued = []

        def enqueue(self, **kwargs):
            self.enqueued.append(kwargs)
            cursor.deterministic_jobs.add(kwargs["resource_id"])
            return {"state": "queued"}

    repository = Repository()

    assert repository.reconcile_confirmed_design_jobs() == 1
    assert repository.reconcile_confirmed_design_jobs() == 0
    assert len(repository.enqueued) == 1


def test_research_design_revision_recovers_only_from_completed_request() -> None:
    cursor = _Cursor(
        {
            "revision": 9,
            "payload": {
                "planningAttempts": [
                    {"requestId": "request-1", "status": "completed"}
                ]
            },
        }
    )
    assert ModelJobQueueRepository._reconciled_business_result(
        cursor, _job("research_design_agent_revision")
    ) == {
        "taskRevision": 9,
        "reconciledFromDurableState": True,
    }


def test_agent_turn_recovers_only_from_completed_turn_request() -> None:
    updated = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    cursor = _Cursor(
        {
            "status": "ready",
            "payload": {"turnRequests": {"request-1": "completed"}},
            "updated_at": updated,
        }
    )
    assert ModelJobQueueRepository._reconciled_business_result(
        cursor, _job("agent_session_turn")
    ) == {
        "sessionStatus": "ready",
        "updatedAt": updated.isoformat(),
        "reconciledFromDurableState": True,
    }


def test_absence_of_completion_proof_stays_unknown_candidate() -> None:
    cursor = _Cursor(
        {
            "revision": 4,
            "payload": {
                "planningAttempts": [
                    {"requestId": "request-1", "status": "failed"}
                ]
            },
        }
    )
    assert ModelJobQueueRepository._reconciled_business_result(
        cursor, _job("research_design_plan_revision")
    ) is None


def test_queue_source_has_no_automatic_requeue_after_execution_started() -> None:
    source = (
        Path(__file__).parents[1] / "app" / "modules" / "model_job_queue.py"
    ).read_text(encoding="utf-8")
    assert "attempt_count < max_attempts THEN 'queued'" not in source
    assert "external_result_unknown" in source
    assert "_reconciled_business_result" in source

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.modules.model_job_queue import ModelJobQueueRepository


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


class _ReconciliationCursor:
    def __init__(self) -> None:
        self.sql = ""

    def execute(self, sql, _params) -> None:
        self.sql = sql

    def fetchall(self):
        return []


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


def test_confirmation_reconciliation_ignores_pre_workflow_admission_payloads() -> None:
    cursor = _ReconciliationCursor()
    repository = _ReconciliationRepository(_ReconciliationConnection(cursor))

    assert repository.reconcile_confirmed_design_jobs() == 0
    assert "task.payload ? 'workflowAdmission'" in cursor.sql
    assert "execution-policy.v2.automatic_research" in cursor.sql


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
        Path(__file__).parents[2] / "model_job_queue.py"
    ).read_text(encoding="utf-8")
    assert "attempt_count < max_attempts THEN 'queued'" not in source
    assert "external_result_unknown" in source
    assert "_reconciled_business_result" in source

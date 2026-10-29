"""Focused resilience coverage for global DB authority versus task-level faults."""

from __future__ import annotations

import json

import pytest
from psycopg import OperationalError

from app.modules.registry_snapshot_repository import RegistrySnapshotIntegrityError
from worker import entrypoint


class _PreclaimFailureRepository:
    def __init__(self, *, fail_at: str) -> None:
        self.fail_at = fail_at

    def reconcile_expired_leases(self) -> None:
        if self.fail_at == "reconcile":
            raise OperationalError("postgresql://user:database-secret@db.invalid/openair")

    def claim(self, *_args, **_kwargs):
        if self.fail_at == "claim":
            raise OperationalError("postgresql://user:database-secret@db.invalid/openair")
        return {"requestId": "request-1", "leaseEpoch": 1}


def test_research_start_database_authority_failures_propagate_without_secret_leak(
    monkeypatch, capsys
):
    for fail_at in ("reconcile", "claim"):
        monkeypatch.setattr(
            entrypoint,
            "ResearchStartRepository",
            lambda fail_at=fail_at: _PreclaimFailureRepository(fail_at=fail_at),
        )

        with pytest.raises(OperationalError):
            entrypoint._claim_and_run_research_start()

        event = json.loads(capsys.readouterr().out)
        assert event == {
            "event": "research_start_global_db_failure",
            "workerId": entrypoint.WORKER_ID,
        }
        assert "database-secret" not in json.dumps(event)

    monkeypatch.setattr(
        entrypoint,
        "ResearchStartRepository",
        lambda: _PreclaimFailureRepository(fail_at="none"),
    )
    post_claim_error = OperationalError("post-claim failure")

    def raise_post_claim_operational_error(claim, **_kwargs):
        assert claim == {"requestId": "request-1", "leaseEpoch": 1}
        raise post_claim_error

    monkeypatch.setattr(
        entrypoint,
        "process_research_start_claim",
        raise_post_claim_operational_error,
    )

    with pytest.raises(OperationalError) as caught:
        entrypoint._claim_and_run_research_start()

    assert caught.value is post_claim_error


def test_global_db_failure_exits_without_emitting_worker_readiness(monkeypatch, capsys):
    post_claim_error = OperationalError("post-claim failure")

    def raise_post_claim_error(_queue_cursor: int):
        raise post_claim_error

    monkeypatch.setattr(entrypoint, "_validate_startup", lambda: None)
    monkeypatch.setattr(entrypoint, "_verify_api_startup_contract", lambda: None)
    monkeypatch.setattr(entrypoint, "_poll_once", raise_post_claim_error)

    with pytest.raises(RuntimeError, match="database authority unavailable") as caught:
        entrypoint.run_forever()

    assert caught.value.__cause__ is post_claim_error
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [
        {
            "event": "worker_process_started",
            "workerId": entrypoint.WORKER_ID,
            "actualPid": entrypoint.os.getpid(),
        },
        {
            "event": "worker_global_db_failure",
            "workerId": entrypoint.WORKER_ID,
        },
    ]


def test_bad_snapshot_request_is_terminalized_once_without_stopping_worker(monkeypatch, capsys):
    failures: list[dict[str, object]] = []

    class Repository:
        settled = False

        def reconcile_expired_leases(self) -> None:
            return None

        def claim(self, *_args, **_kwargs):
            if self.settled:
                return None
            return {"requestId": "request-bad-snapshot", "leaseEpoch": 7}

        def fail_request(self, request_id, worker_id, lease_epoch, **kwargs):
            self.settled = True
            failures.append({
                "requestId": request_id,
                "workerId": worker_id,
                "leaseEpoch": lease_epoch,
                **kwargs,
            })

    repository = Repository()
    monkeypatch.setattr(entrypoint, "ResearchStartRepository", lambda: repository)

    def fail_for_bad_snapshot(*_args, **_kwargs):
        raise RegistrySnapshotIntegrityError("snapshot staticPolicies shape is invalid")

    monkeypatch.setattr(entrypoint, "process_research_start_claim", fail_for_bad_snapshot)

    assert entrypoint._claim_and_run_research_start() is True
    assert entrypoint._claim_and_run_research_start() is False
    assert failures == [{
        "requestId": "request-bad-snapshot",
        "workerId": entrypoint.WORKER_ID,
        "leaseEpoch": 7,
        "failure_code": "registry_snapshot_integrity_invalid",
        "user_message": "该研究请求的持久化记录无法安全处理，未执行任何外部操作。",
    }]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1] == {
        "event": "research_start_request_failed",
        "requestId": "request-bad-snapshot",
        "workerId": entrypoint.WORKER_ID,
        "leaseEpoch": 7,
        "failureCode": "registry_snapshot_integrity_invalid",
    }

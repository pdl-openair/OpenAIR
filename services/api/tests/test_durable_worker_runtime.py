from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.routes import internal
from app.execution import durable_worker_runtime
from app.execution.openai_responses_provider import ProviderContractError
from app.modules.artifact_write import ArtifactStorageError
from app.modules.durable_execution import DurableExecutionRepository


def test_durable_orchestrate_endpoint_fails_closed_before_execution_context_or_provider(monkeypatch) -> None:
    monkeypatch.setattr(internal.settings, "worker_auth_token", "valid-worker-token")

    class ForbiddenRepository:
        def __init__(self):
            pytest.fail("fail-closed endpoint instantiated the durable repository")

    monkeypatch.setattr(internal, "DurableExecutionRepository", ForbiddenRepository)
    monkeypatch.setattr(
        internal,
        "prepare_durable_orchestration",
        lambda *_args, **_kwargs: pytest.fail("fail-closed endpoint prepared a provider call"),
    )
    monkeypatch.setattr(
        internal,
        "execute_durable_skill",
        lambda *_args, **_kwargs: pytest.fail("fail-closed endpoint executed a Skill"),
    )

    with pytest.raises(HTTPException) as exc:
        internal.durable_orchestrate(
            "run-fail-closed",
            internal.DurableLeaseIn(workerId="worker-1", leaseEpoch=4),
            x_openair_worker_token="valid-worker-token",
        )

    assert 400 <= exc.value.status_code < 500
    assert exc.value.detail == "durable_orchestration_worker_process_only"


def test_frozen_schema_failure_preserves_all_safe_paths_as_protected_diagnostics(monkeypatch) -> None:
    captured = []

    class EvidenceRepository:
        def __init__(self, **_kwargs) -> None:
            pass

        def put(self, envelope) -> None:
            captured.append(envelope)

    monkeypatch.setattr(internal, "ResearchExternalEvidenceRepository", EvidenceRepository)
    error = ProviderContractError("provider_response_contract_invalid")
    error.safe_error_code = "artifact_schema_invalid"
    error.safe_field_paths = ("/result/missing", "/result/extra")
    error.evidence_body = b"fictional-sensitive-response"
    result = internal._capture_provider_contract_evidence(
        durable=type("Durable", (), {"_connect": staticmethod(lambda: None)})(),
        prepared=type("Prepared", (), {"adapter": "huiyan_chat_completions", "provider_id": "openai_primary"})(),
        unit_request={
            "taskId": "task", "ownerId": "owner", "workspaceId": "workspace",
            "providerEvidence": {
                "executionDecisionId": "decision", "workUnitId": "unit", "capabilityCallId": "call",
                "dispatchId": "dispatch", "attemptNumber": 1, "canonicalPayloadFingerprint": "a" * 64,
            },
        },
        error=error,
    )

    assert result["errorCode"] == "artifact_schema_invalid"
    assert result["path"] == "result.missing"
    assert len(captured) == 1
    assert [item.error_code for item in captured[0].diagnostics] == ["artifact_schema_invalid"] * 2
    assert [item.field_path for item in captured[0].diagnostics] == ["result.missing", "result.extra"]
    assert captured[0].byte_length == 0
    assert "fictional-sensitive-response" not in repr(result)


def test_worker_runtime_preserves_run_worker_and_fence_identity(monkeypatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def execute(run_id: str, body: internal.DurableLeaseIn) -> dict:
        calls.append((run_id, body.workerId, body.leaseEpoch))
        return {"knownFailure": "external_result_unknown"}

    monkeypatch.setattr(durable_worker_runtime, "_execute_durable_orchestration_in_worker", execute)

    result = durable_worker_runtime.process_durable_worker_run("run-unknown", "worker-7", 12)

    assert result == {"knownFailure": "external_result_unknown"}
    assert calls == [("run-unknown", "worker-7", 12)]


def test_artifact_storage_failure_closes_the_fenced_run_without_provider_replay(monkeypatch) -> None:
    failures: list[tuple[str, str, int]] = []

    class Repository:
        def read_orchestration_execution_context(self, run_id, worker_id, lease_epoch):
            return (
                {
                    "run_id": run_id,
                    "task_id": "task-storage",
                    "owner_id": "owner-storage",
                    "workspace_id": "workspace-storage",
                },
                {"workUnits": []},
            )

        def fail_artifact_storage(self, run_id, worker_id, lease_epoch):
            failures.append((run_id, worker_id, lease_epoch))
            return {"state": "failed"}

    class Orchestrator:
        def __init__(self, **_kwargs) -> None:
            pass

        def run(self, **_kwargs):
            raise ArtifactStorageError("artifact_storage_write_failed")

    repository = Repository()
    monkeypatch.setattr(internal.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(internal, "DurableExecutionRepository", lambda: repository)
    monkeypatch.setattr(internal, "DurableMultiAgentOrchestrator", Orchestrator)
    monkeypatch.setattr(internal, "serialize_run", lambda run: run)
    monkeypatch.setattr(
        internal,
        "execute_prepared_durable_orchestration",
        lambda *_args, **_kwargs: pytest.fail("storage recovery replayed the provider"),
    )

    result = internal._execute_durable_orchestration_in_worker(
        "run-storage",
        internal.DurableLeaseIn(workerId="worker-storage", leaseEpoch=3),
    )

    assert result == {
        "knownFailure": "artifact_storage_failed_after_provider_result",
        "run": {"state": "failed"},
    }
    assert failures == [("run-storage", "worker-storage", 3)]


@pytest.mark.parametrize(
    "caller_cutoff",
    [None, datetime(2026, 7, 25, 8, 55, tzinfo=timezone.utc)],
)
def test_durable_claim_enforces_server_cutoff_as_minimum(monkeypatch, caller_cutoff) -> None:
    server_cutoff = datetime(2026, 7, 26, 8, 55, tzinfo=timezone.utc)
    observed: dict[str, object] = {}

    class Repository:
        def claim(self, worker_id, lease_seconds, *, not_before=None):
            observed.update(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                not_before=not_before,
            )
            return None

    monkeypatch.setattr(internal.settings, "worker_auth_token", "valid-worker-token")
    monkeypatch.setattr(internal.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(internal.settings, "env", "production")
    monkeypatch.setenv("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", server_cutoff.isoformat())
    monkeypatch.setattr(internal, "DurableExecutionRepository", Repository)

    result = internal.durable_claim(
        internal.DurableClaimIn(workerId="worker-1", notBefore=caller_cutoff),
        x_openair_worker_token="valid-worker-token",
    )

    assert result == {"run": None}
    assert observed["not_before"] == server_cutoff


def test_durable_claim_rejects_timezone_naive_cutoff() -> None:
    with pytest.raises(ValueError, match="timezone"):
        internal.DurableClaimIn(workerId="worker-1", notBefore="2026-07-26T08:55:00")


def test_durable_repository_executes_cutoff_queries_with_bound_parameter(monkeypatch) -> None:
    cutoff = datetime(2026, 7, 26, 8, 55, tzinfo=timezone.utc)

    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self.current_sql = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=()):
            self.current_sql = sql
            self.calls.append((sql, params))

        def fetchall(self):
            if "WHERE NOT EXISTS (" in self.current_sql and "r.created_at >= %s" in self.current_sql:
                return [{"run_id": "run-1", "task_id": "task-1"}]
            return []

        def fetchone(self):
            if "SELECT * FROM openair_execution_runs r WHERE run_id" in self.current_sql:
                return {
                    "run_id": "run-1",
                    "task_id": "task-1",
                    "lease_epoch": 0,
                }
            if "UPDATE openair_execution_runs SET state = 'leased'" in self.current_sql:
                return {
                    "run_id": "run-1",
                    "task_id": "task-1",
                    "lease_epoch": 1,
                }
            return None

    class Connection:
        def __init__(self, cursor: Cursor) -> None:
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    cursor = Cursor()
    repository = DurableExecutionRepository()
    monkeypatch.setattr(repository, "_locked_task", lambda *_args: None)
    monkeypatch.setattr(repository, "_event", lambda *_args, **_kwargs: None)

    @contextmanager
    def connect():
        yield Connection(cursor)

    monkeypatch.setattr(repository, "_connect", connect)

    assert repository.claim("worker-1", 60, not_before=cutoff)["run_id"] == "run-1"

    cutoff_calls = [(sql, params) for sql, params in cursor.calls if "r.created_at >= %s" in sql]
    assert len(cutoff_calls) == 2
    assert all("{run_cutoff_sql}" not in sql for sql, _params in cutoff_calls)
    assert cutoff_calls[0][1][-1] == cutoff
    locked_params = cutoff_calls[1][1]
    assert len(locked_params) == 3
    assert locked_params[0] == "run-1"
    assert locked_params[2] == cutoff

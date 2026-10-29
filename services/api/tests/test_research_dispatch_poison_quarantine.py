from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.research_orchestration import ResearchTaskV12
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
)


_CONTRACT_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _binding_task() -> ResearchTaskV12:
    payload = json.loads(_CONTRACT_FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    decision = payload["executionDecisions"][0]
    assignment = decision["assignments"][0]
    work_unit = payload["workUnits"][0]
    payload["substageRuntimeStates"][0]["status"] = "queued"
    work_unit.update({"status": "queued", "finishedAt": None})
    agent_ref = {"agentId": "agent-cold-start", "definitionVersion": 1}
    actor_ref = {
        "actorType": "agent",
        "actorId": "agent-cold-start",
        "definitionVersion": 1,
    }
    executor_ref = {
        "executorType": "agent",
        "executorId": "agent-cold-start",
        "definitionVersion": 1,
    }
    decision.update({"strategy": "specialist_agent"})
    decision["candidateEvaluations"][0].update(
        {
            "candidateRef": agent_ref,
            "supportedRole": "primary",
            "sourceBindingRefs": [
                {"bindingId": "binding-agent-cold-start", "bindingVersion": 1}
            ],
        }
    )
    assignment.update(
        {
            "sourceBindingRefs": [
                {"bindingId": "binding-agent-cold-start", "bindingVersion": 1}
            ],
            "responsibleActorRef": actor_ref,
            "executorRef": executor_ref,
        }
    )
    work_unit.update(
        {
            "responsibleActorRef": actor_ref,
            "executorRef": executor_ref,
        }
    )
    decision["runtimeReadiness"][0].update(
        {
            "capabilityRef": agent_ref,
            "runtimeBindingId": "registry:binding-agent-cold-start:1",
        }
    )
    return ResearchTaskV12.model_validate(payload)


class _BindingRepository:
    def __init__(self, task: ResearchTaskV12, snapshot: dict | None = None) -> None:
        self.task = task
        self.snapshot = snapshot or {}

    def load(self, **_scope):
        return self.task

    def load_registry_snapshot(self, _content_hash):
        return deepcopy(self.snapshot)


def _dispatch_candidate(task: ResearchTaskV12) -> dict:
    decision = task.execution_decisions[0]
    work_unit = task.work_units[0]
    return {
        "dispatch_id": "dispatch-cold-start",
        "task_id": task.task_id,
        "execution_decision_id": decision.execution_decision_id,
        "work_unit_id": work_unit.work_unit_id,
        "idempotency_key": work_unit.idempotency_key,
        "status": "pending",
        "owner_id": "owner",
        "workspace_id": "workspace",
        "revision": task.revision,
    }


def _binding_snapshot() -> dict:
    return {
        "definitions": {
            "agents": [
                {
                    "agentId": "agent-cold-start",
                    "definitionVersion": 1,
                    "enabled": True,
                    "defaultProvider": "provider-safe",
                    "allowedProviders": ["provider-safe"],
                }
            ]
        },
        "bindings": {
            "agents": [
                {
                    "bindingId": "binding-agent-cold-start",
                    "bindingVersion": 1,
                    "agentId": "agent-cold-start",
                    "agentDefinitionVersion": 1,
                    "enabled": True,
                }
            ]
        },
    }


def test_strict_task_contract_still_rejects_historical_research_understanding_shape() -> None:
    task = _binding_task()
    payload = task.model_dump(mode="json", by_alias=True)
    understanding = payload["researchDesignVersions"][0]["understanding"]
    understanding.pop("primaryResearchQuestions")
    understanding.pop("auxiliaryResearchDimensions")

    with pytest.raises(ValidationError):
        ResearchTaskV12.model_validate(payload)
    with pytest.raises(
        ResearchOrchestrationPersistenceError,
        match="invalid_research_orchestration_payload",
    ):
        ResearchOrchestrationRepository._task_from_database_payload(
            payload,
            int(payload["revision"]),
        )


def test_candidate_query_excludes_durably_quarantined_submission_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    observed: list[str] = []

    class Result:
        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params=None):
            observed.append(" ".join(str(query).split()))
            return Result()

    monkeypatch.setattr(entrypoint.psycopg, "connect", lambda *_args, **_kwargs: Connection())
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(
        repository=_BindingRepository(_binding_task())
    )

    assert resolver._candidate_rows() == []
    sql = observed[0]
    assert "d.status = 'submission_unknown'" in sql
    assert "d.result_storage_ref IS NOT NULL" in sql
    assert (
        "d.last_error_code IS DISTINCT FROM 'invalid_research_orchestration_payload'"
        in sql
    )


def test_authorized_recovery_query_excludes_durably_quarantined_submission_unknown() -> None:
    observed: list[str] = []

    class Cursor:
        def __init__(self) -> None:
            self.mode = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params=None):
            sql = " ".join(str(query).split())
            observed.append(sql)
            self.mode = "candidates" if "d.status AS observed_status" in sql else "other"
            return self

        def fetchall(self):
            return [] if self.mode == "candidates" else []

    cursor = Cursor()

    class Connection:
        def cursor(self):
            return cursor

    class Uow:
        @contextmanager
        def transaction(self):
            yield Connection()

    repository = ResearchOrchestrationRepository.__new__(ResearchOrchestrationRepository)
    repository._uow = Uow()
    repository._settings = type("Settings", (), {"execution_namespace": "workspace-test"})()
    repository._require_write_enabled = lambda: None
    repository._require_postgres = lambda: None

    assert repository.claim_authorized_recovery(
        lease_owner="worker-test",
        lease_seconds=60,
        execution_namespace="workspace-test",
    ) == []
    sql = next(row for row in observed if "d.status AS observed_status" in row)
    assert (
        "d.last_error_code IS DISTINCT FROM 'invalid_research_orchestration_payload'"
        in sql
    )


def test_invalid_historical_task_poison_does_not_block_next_valid_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task()

    class Repository(_BindingRepository):
        def __init__(self):
            super().__init__(task, _binding_snapshot())
            self.failures: list[dict] = []

        def load(self, *, task_id: str, **_scope):
            if task_id == "task-historical-invalid":
                raise ResearchOrchestrationPersistenceError(
                    "invalid_research_orchestration_payload"
                )
            return self.task

        def record_dispatch_resolution_failure(self, **kwargs):
            self.failures.append(kwargs)
            return "submission_unknown"

    repository = Repository()
    valid = {**_dispatch_candidate(task), "attempt_count": 0}
    poison = {
        **valid,
        "dispatch_id": "dispatch-historical-invalid",
        "task_id": "task-historical-invalid",
        "status": "submission_unknown",
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": datetime.now(timezone.utc),
        "result_storage_ref": "durable/research-dispatch/dispatch-historical-invalid/result.json",
        "attempt_count": 2,
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    resolver._candidate_rows = lambda: [poison, valid]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")

    binding = resolver.resolve()

    assert binding is not None
    assert binding.dispatch_id == valid["dispatch_id"]
    assert len(repository.failures) == 1
    assert repository.failures[0]["dispatch_id"] == poison["dispatch_id"]


def test_invalid_submission_unknown_poison_never_reaches_provider_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker import entrypoint

    task = _binding_task()

    class Repository(_BindingRepository):
        def __init__(self):
            super().__init__(task)
            self.failures: list[dict] = []

        def load(self, **_scope):
            raise ResearchOrchestrationPersistenceError(
                "invalid_research_orchestration_payload"
            )

        def record_dispatch_resolution_failure(self, **kwargs):
            self.failures.append(kwargs)
            return "submission_unknown"

    repository = Repository()
    poison = {
        **_dispatch_candidate(task),
        "dispatch_id": "dispatch-historical-invalid-no-replay",
        "status": "submission_unknown",
        "submission_mode": "single_attempt_no_replay",
        "external_action_started_at": datetime.now(timezone.utc),
        "result_storage_ref": (
            "durable/research-dispatch/dispatch-historical-invalid-no-replay/result.json"
        ),
        "attempt_count": 2,
    }
    resolver = entrypoint.PostgresDispatchProviderBindingResolver(repository=repository)
    resolver._candidate_rows = lambda: [poison]
    monkeypatch.setattr(entrypoint.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(entrypoint.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(entrypoint.settings, "workunit_execution_mode", "worker")
    monkeypatch.setattr(
        entrypoint,
        "_provider_for_dispatch_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("poison dispatch must not construct or submit a provider")
        ),
    )

    handled = entrypoint.claim_and_run_research_dispatch(
        binding_resolver=resolver,
        service=object(),
    )

    assert handled is False
    assert len(repository.failures) == 1

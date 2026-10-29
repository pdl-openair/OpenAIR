from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.execution import model_job_worker
from app.modules import research_execution_decision_service as decision_service
from app.modules.research_design_activation import runtime_states_for_confirmed_design
from app.modules.research_execution_decision_service import (
    ExecutionPreflightBlocked,
    ResearchExecutionDecisionService,
    promote_dependency_ready_substages,
)
from app.modules.research_orchestration_resolver import ResolutionRejectedError
from tests.support.research_execution_decision import build_planned_continuation_task

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def _snapshot(task, *, enabled: bool):
    design = task.research_design_versions[-1]
    return {
        "definitions": {
            "substages": [
                {
                    "definitionId": row.substage_definition_id,
                    "definitionVersion": row.substage_definition_version,
                    "enabled": enabled,
                }
                for row in design.instances
            ]
        }
    }


def test_disabled_definition_is_not_initially_ready_and_enabled_peer_is_ready():
    task = build_planned_continuation_task(next_stage_key="execution_validation")
    design = task.research_design_versions[-1]
    no_dependency = next(row for row in design.instances if not row.dependencies)

    disabled = runtime_states_for_confirmed_design(
        design=design,
        existing_states=[],
        activated_at=NOW,
        snapshot=_snapshot(task, enabled=False),
    )
    disabled_runtime = next(
        row for row in disabled if row.substage_instance_id == no_dependency.substage_instance_id
    )
    assert disabled_runtime.status.value == "planned"
    assert disabled_runtime.status_reason_code == "definition_disabled"

    enabled = runtime_states_for_confirmed_design(
        design=design,
        existing_states=[],
        activated_at=NOW,
        snapshot=_snapshot(task, enabled=True),
    )
    enabled_runtime = next(
        row for row in enabled if row.substage_instance_id == no_dependency.substage_instance_id
    )
    assert enabled_runtime.status.value == "ready"
    assert enabled_runtime.status_reason_code is None


def test_disabled_definition_does_not_promote_after_dependencies_satisfied():
    task = build_planned_continuation_task(next_stage_key="execution_validation")
    disabled = promote_dependency_ready_substages(
        task,
        occurred_at=NOW,
        snapshot=_snapshot(task, enabled=False),
    )
    downstream = next(
        row for row in disabled.substage_runtime_states if row.substage_instance_id == "sub_downstream"
    )
    assert downstream.status.value == "planned"
    assert downstream.status_reason_code == "definition_disabled"

    enabled = promote_dependency_ready_substages(
        task,
        occurred_at=NOW,
        snapshot=_snapshot(task, enabled=True),
    )
    downstream = next(
        row for row in enabled.substage_runtime_states if row.substage_instance_id == "sub_downstream"
    )
    assert downstream.status.value == "ready"


def test_disabled_ready_runtime_is_excluded_from_execution_candidates(monkeypatch):
    task = build_planned_continuation_task(next_stage_key="execution_validation")
    task = promote_dependency_ready_substages(
        task,
        occurred_at=NOW,
        snapshot=_snapshot(task, enabled=True),
    )
    design = task.research_design_versions[-1]
    service = ResearchExecutionDecisionService(
        repository=SimpleNamespace(load_catalog_execution_binding=lambda **_: None),
        resolver=object(),
        facts_source=lambda *_: (SimpleNamespace(), {}),
    )

    with pytest.raises(ExecutionPreflightBlocked) as caught:
        service._legal_next_task_candidates(
            task=task,
            design=design,
            snapshot=_snapshot(task, enabled=False),
            owner_id="owner",
            workspace_id="workspace",
        )
    assert caught.value.facts["candidateRejections"]["sub_downstream"] == "definition_disabled"

    monkeypatch.setattr(
        decision_service,
        "evaluate_instance_preflight",
        lambda _task, *, instance_id, **_: SimpleNamespace(
            executable=True,
            missing_reason_code=None,
            instance=next(row for row in design.instances if row.substage_instance_id == instance_id),
        ),
    )
    candidates = service._legal_next_task_candidates(
        task=task,
        design=design,
        snapshot=_snapshot(task, enabled=True),
        owner_id="owner",
        workspace_id="workspace",
    )
    assert "sub_downstream" in {row.instance.substage_instance_id for row in candidates}


def test_async_resolution_rejection_preserves_definition_disabled(monkeypatch):
    lease = SimpleNamespace(job_id="job", job_kind="execution_selection", resource_id="task")

    class Queue:
        finished = None
        def reconcile_confirmed_design_jobs(self): pass
        def claim(self, **_): return lease
        def finish(self, _lease, **kwargs): self.finished = kwargs

    queue = Queue()
    monkeypatch.setattr(model_job_worker, "ModelJobQueueRepository", lambda: queue)
    monkeypatch.setattr(model_job_worker, "model_job_lease_seconds", lambda: 120)
    monkeypatch.setattr(
        model_job_worker,
        "_LeaseKeeper",
        lambda *_args, **_kwargs: SimpleNamespace(
            lost=False, start=lambda: None, close=lambda: None
        ),
    )
    monkeypatch.setattr(
        model_job_worker,
        "_execute",
        lambda _lease: (_ for _ in ()).throw(ResolutionRejectedError("definition_disabled")),
    )

    assert model_job_worker.process_model_job_once(worker_id="worker") is True
    assert queue.finished["state"] == "blocked"
    assert queue.finished["error_code"] == "definition_disabled"
    assert queue.finished["result_payload"]["reasonCode"] == "definition_disabled"
    assert queue.finished["error_code"] != "model_job_system_failure"


def test_formal_continuation_reconcile_loads_frozen_snapshot_and_keeps_disabled_planned(monkeypatch):
    from contextlib import contextmanager

    from app.modules import repeatable_definition_reentry
    from app.modules import research_execution_decision_service as lifecycle_service
    from app.modules.research_orchestration_repository import ResearchOrchestrationRepository

    task = build_planned_continuation_task(next_stage_key="execution_validation")
    disabled_snapshot = _snapshot(task, enabled=False)
    written = {}
    loaded_hashes = []

    class Cursor:
        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

    class Uow:
        @contextmanager
        def transaction(self):
            yield Connection()

    class RepoHarness:
        prepare_execution_continuation = ResearchOrchestrationRepository.prepare_execution_continuation
        _reconcile_task_lifecycle_with_frozen_snapshot = (
            ResearchOrchestrationRepository._reconcile_task_lifecycle_with_frozen_snapshot
        )

        def __init__(self):
            self._uow = Uow()

        def _require_write_enabled(self):
            return None

        def _require_postgres(self):
            return None

        def _load_locked_task(self, _cursor, **_kwargs):
            return task, task.model_dump(mode="json", by_alias=True)

        def _execution_stop_active(self, _raw_payload):
            return False

        def _apply_pending_execution_policy_at_boundary(self, _cursor, *, task, **_kwargs):
            return task, {"current_mode": "automatic_research"}

        def load_registry_snapshot(self, content_hash):
            loaded_hashes.append(content_hash)
            return disabled_snapshot

        def _write_locked_task(self, _cursor, _raw_payload, projected):
            written["task"] = projected

    monkeypatch.setattr(
        lifecycle_service,
        "reconcile_persisted_accepted_revision_progress",
        lambda task, **_kwargs: task,
    )
    monkeypatch.setattr(
        repeatable_definition_reentry,
        "materialize_repeatable_definition_reentries",
        lambda task, **_kwargs: task,
    )

    repo = RepoHarness()
    directive = repo.prepare_execution_continuation(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        completed_execution_decision_id=task.execution_decisions[0].execution_decision_id,
        changed_by="test",
    )

    assert loaded_hashes == [task.registry_snapshot_ref.content_hash]
    assert directive.action == "none"
    projected = written["task"]
    downstream = next(
        row
        for row in projected.substage_runtime_states
        if row.substage_instance_id == "sub_downstream"
    )
    assert downstream.status.value == "planned"
    assert downstream.status_reason_code == "definition_disabled"


def test_second_production_reconcile_entry_uses_frozen_snapshot(monkeypatch):
    from contextlib import contextmanager
    from copy import deepcopy

    from app.models.research_orchestration import ResearchTaskV12
    from app.modules import research_orchestration_repository as repository_module
    from app.modules.research_orchestration_repository import ResearchOrchestrationRepository

    original = build_planned_continuation_task(next_stage_key="execution_validation")
    payload = original.model_dump(mode="json", by_alias=True)
    design = payload["researchDesignVersions"][-1]
    downstream = next(
        row for row in design["instances"] if row["substageInstanceId"] == "sub_downstream"
    )
    second = deepcopy(downstream)
    second["substageInstanceId"] = "sub_disabled_second"
    design["instances"].append(second)

    downstream_runtime = next(
        row
        for row in payload["substageRuntimeStates"]
        if row["substageInstanceId"] == "sub_downstream"
    )
    downstream_runtime.update(
        {
            "status": "ready",
            "statusReasonCode": None,
            "updatedAt": NOW.isoformat(),
        }
    )
    second_runtime = deepcopy(downstream_runtime)
    second_runtime.update(
        {
            "substageInstanceId": "sub_disabled_second",
            "status": "planned",
            "statusReasonCode": None,
        }
    )
    payload["substageRuntimeStates"].append(second_runtime)
    task = ResearchTaskV12.model_validate(payload)
    disabled_snapshot = _snapshot(task, enabled=False)
    written = {}
    loaded_hashes = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

    class Uow:
        @contextmanager
        def transaction(self):
            yield Connection()

    class RepoHarness:
        record_execution_continuation_blocked = (
            ResearchOrchestrationRepository.record_execution_continuation_blocked
        )
        _reconcile_task_lifecycle_with_frozen_snapshot = (
            ResearchOrchestrationRepository._reconcile_task_lifecycle_with_frozen_snapshot
        )

        def __init__(self):
            self._uow = Uow()

        def _require_write_enabled(self):
            return None

        def _require_postgres(self):
            return None

        def _require_expected_revision(self, _revision):
            return None

        def _load_locked_task(self, _cursor, **_kwargs):
            return task, task.model_dump(mode="json", by_alias=True)

        def load_registry_snapshot(self, content_hash):
            loaded_hashes.append(content_hash)
            return disabled_snapshot

        def _write_locked_task(self, _cursor, _raw_payload, projected):
            written["task"] = projected

    monkeypatch.setattr(repository_module, "needs_user_input", lambda _reason: False)
    monkeypatch.setattr(
        repository_module,
        "runtime_blocker_status",
        lambda _reason: "blocked_recoverable",
    )

    repo = RepoHarness()
    revision = repo.record_execution_continuation_blocked(
        task_id=task.task_id,
        owner_id="owner",
        workspace_id="workspace",
        expected_task_revision=task.revision,
        next_instance_id="sub_downstream",
        reason_code="test_blocker",
    )

    assert revision == task.revision + 1
    assert loaded_hashes == [task.registry_snapshot_ref.content_hash]
    projected = written["task"]
    second_after = next(
        row
        for row in projected.substage_runtime_states
        if row.substage_instance_id == "sub_disabled_second"
    )
    assert second_after.status.value == "planned"
    assert second_after.status_reason_code == "definition_disabled"

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from app.modules.continuation_responsibility import (
    ContinuationOwnerKind,
    ContinuationOwnerRef,
    ContinuationResponsibilityKind,
)
from app.modules.durable_execution import DurableExecutionRepository
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import (
    DispatchContinuationFence,
    ExecutionContinuationDirective,
)


class RecordingCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.rowcount = 1

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statements.append((statement, parameters))


def test_legacy_projection_cannot_write_task_lifecycle_or_erase_prior_value() -> None:
    cursor = RecordingCursor()
    task = {
        "task_id": "task-t16",
        "revision": 7,
        "status": "cancelled",
        "payload": {
            "taskId": "task-t16",
            "revision": 7,
            "status": "running",
            "capabilityCalls": [{"id": "call-1", "status": "running"}],
            "artifacts": [{"id": "artifact-prior", "artifactState": "usable"}],
            "scientificState": {"acceptedClaims": ["prior-value"]},
        },
    }

    projected = DurableExecutionRepository()._project_task(
        cursor,
        task,
        call_id="call-1",
        call_status="failed",
        unknown_external_result=True,
    )

    assert projected["status"] == "cancelled"
    assert projected["artifacts"] == [{"id": "artifact-prior", "artifactState": "usable"}]
    assert projected["scientificState"] == {"acceptedClaims": ["prior-value"]}
    assert projected["executionRecovery"]["owner"] == "system"
    assert projected["executionRecovery"]["allowedActions"] == []
    statement, _parameters = cursor.statements[-1]
    assert "SET status" not in statement


def test_legacy_callback_has_no_task_status_parameter_or_terminal_mapping() -> None:
    signature = inspect.signature(DurableExecutionRepository._project_task)
    callback_source = inspect.getsource(DurableExecutionRepository.callback)
    start_source = inspect.getsource(DurableExecutionRepository.start_initial)

    assert "status" not in signature.parameters
    assert "task_status" not in callback_source
    assert 'status="cancelled"' not in callback_source
    assert 'status="failed"' not in callback_source
    assert "UPDATE openair_tasks SET status" not in start_source


def test_unknown_branch_keeps_system_owner_after_unrelated_ready_work_is_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-unknown",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unit-blocked",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=2,
    )
    blocked_assignment = SimpleNamespace(
        assignment_id="assignment-blocked", dependency_assignment_ids=[]
    )
    unrelated_assignment = SimpleNamespace(
        assignment_id="assignment-unrelated", dependency_assignment_ids=[]
    )
    decision = SimpleNamespace(
        execution_decision_id="decision-1",
        status=SimpleNamespace(value="dispatched"),
        assignments=[blocked_assignment, unrelated_assignment],
    )
    blocked_work = SimpleNamespace(
        work_unit_id="work-unit-blocked",
        assignment_id="assignment-blocked",
        execution_decision_id="decision-1",
        source_mode=SimpleNamespace(value="live"),
        substage_instance_revision=1,
    )
    unrelated_work = SimpleNamespace(
        work_unit_id="work-unit-unrelated",
        assignment_id="assignment-unrelated",
        execution_decision_id="decision-1",
        source_mode=SimpleNamespace(value="live"),
        substage_instance_revision=1,
    )
    before = SimpleNamespace(revision=11, execution_decisions=[decision], work_units=[blocked_work])
    after = SimpleNamespace(
        revision=12,
        execution_decisions=[decision],
        work_units=[blocked_work, unrelated_work],
    )

    class Repository:
        def load(self, **_scope):
            return before

        @staticmethod
        def system_external_recovery_state(observed_fence):
            assert observed_fence == fence
            return "unknown"

        def load_continuation_owner_for_work_unit(self, **kwargs):
            assert kwargs["work_unit_id"] == "work-unit-unrelated"
            return ContinuationOwnerRef(
                kind=ContinuationOwnerKind.DISPATCH,
                owner_id="dispatch-unrelated",
                execution_decision_id="decision-1",
                work_unit_id="work-unit-unrelated",
            )

        def prepare_execution_continuation(self, **_kwargs):
            events.append("continuation")
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=12,
                next_instance_id=None,
                selection_request_id=None,
                reason_code=None,
            )

    class Coordinator:
        def release_ready(self, **kwargs):
            events.append("release")
            assert kwargs["expected_task_revision"] == 11
            return after

    class QueryUnsupportedRecovery:
        @staticmethod
        def capabilities():
            return SimpleNamespace(query_supported=False)

    service = ResearchDispatchService(
        Repository(),  # type: ignore[arg-type]
        decision_coordinator=Coordinator(),
        external_recovery_service=QueryUnsupportedRecovery(),
    )
    monkeypatch.setattr(
        service, "_recover_confirmed_semantic_material_gap", lambda *_args, **_kwargs: False
    )

    proof = service.process_continuation(fence)

    assert proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
    assert proof.reason_code == "same_decision_dependency_released"
    assert proof.successor_work_unit_id == "work-unit-unrelated"
    assert proof.owner_id == "dispatch-unrelated"
    assert events == ["release", "continuation"]


def test_system_owned_reconcile_queries_without_user_action() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-unknown",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unknown",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="continuation-worker",
        attempt_count=3,
    )
    command = object()

    class SystemRepository:
        @staticmethod
        def system_external_recovery_state(observed_fence):
            assert observed_fence == fence
            return "unknown"

        def prepare_system_reconcile_command(self, observed_fence):
            assert observed_fence == fence
            return command

    class SystemRecovery:
        def __init__(self) -> None:
            self.commands = []

        @staticmethod
        def capabilities():
            return SimpleNamespace(query_supported=True)

        def reconcile(self, observed_command):
            self.commands.append(observed_command)
            return {"status": "recorded", "externalState": "running"}

    recovery = SystemRecovery()
    service = ResearchDispatchService(
        SystemRepository(),  # type: ignore[arg-type]
        external_recovery_service=recovery,
    )

    pending = service._system_reconcile_external_result(fence)

    assert pending is True
    assert recovery.commands == [command]


def test_query_unsupported_attempts_providerless_recovery_before_retaining_obligation() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-unknown",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unknown",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="continuation-worker",
        attempt_count=2,
    )
    authorized = object()

    class SystemRepository:
        @staticmethod
        def system_external_recovery_state(observed_fence):
            assert observed_fence == fence
            return "unknown"

        def load_providerless_recovery_authorization(self, observed_fence):
            assert observed_fence == fence
            return authorized

    class QueryUnsupportedRecovery:
        @staticmethod
        def capabilities():
            return SimpleNamespace(query_supported=False)

    recovery_attempts: list[AuthorizedDispatch] = []
    service = ResearchDispatchService(
        SystemRepository(),  # type: ignore[arg-type]
        external_recovery_service=QueryUnsupportedRecovery(),
    )
    service.recover_completed_result = (  # type: ignore[method-assign]
        lambda observed, **kwargs: (
            recovery_attempts.append(observed) or True
            if observed is authorized
            else False
        )
    )

    assert service._system_reconcile_external_result(fence) is False
    assert recovery_attempts == [authorized]


def test_query_unsupported_requeues_proven_reasoning_through_existing_retry() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-unknown", task_id="task-1",
        execution_decision_id="decision-1", work_unit_id="work-unknown",
        owner_id="owner-1", workspace_id="workspace-1",
        lease_owner="continuation-worker", attempt_count=2,
    )
    command = object()

    class SystemRepository:
        @staticmethod
        def system_external_recovery_state(_fence):
            return "unknown"

        @staticmethod
        def load_providerless_recovery_authorization(_fence):
            return None

        @staticmethod
        def prepare_system_explicit_retry_command(observed, **scope):
            assert observed == fence
            assert scope == {
                "owner_id": "owner-1", "workspace_id": "workspace-1", "error_code": "",
            }
            return command

    class SystemRecovery:
        commands = []

        @staticmethod
        def capabilities():
            return SimpleNamespace(query_supported=False)

        def retry(self, observed):
            self.commands.append(observed)
            return {"status": "created", "attemptNumber": 2}

    recovery = SystemRecovery()
    service = ResearchDispatchService(
        SystemRepository(), external_recovery_service=recovery,
    )
    assert service._system_reconcile_external_result(fence) is True
    assert recovery.commands == [command]


def test_query_unsupported_keeps_system_obligation_without_submission() -> None:
    class SystemRepository:
        @staticmethod
        def system_external_recovery_state(_fence):
            return "unknown"

        def prepare_system_reconcile_command(self, _fence):
            raise AssertionError("query-unsupported providers must not build a query command")

    class SystemRecovery:
        @staticmethod
        def capabilities():
            return SimpleNamespace(query_supported=False)

        @staticmethod
        def reconcile(_command):
            raise AssertionError("query-unsupported providers must not be queried")

    service = ResearchDispatchService(
        SystemRepository(),  # type: ignore[arg-type]
        external_recovery_service=SystemRecovery(),
    )

    assert (
        service._system_reconcile_external_result(
            DispatchContinuationFence(
                dispatch_id="dispatch-unknown",
                task_id="task-1",
                execution_decision_id="decision-1",
                work_unit_id="work-unknown",
                owner_id="owner-1",
                workspace_id="workspace-1",
                lease_owner="continuation-worker",
                attempt_count=4,
            )
        )
        is True
    )

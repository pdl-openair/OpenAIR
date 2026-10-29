"""Unit tests for zombie dispatch-continuation drain and claim deprioritization."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules import research_dispatch_service as dispatch_service_module
from app.modules.continuation_responsibility import (
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
)
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import DispatchContinuationFence


def _no_responsibility_proof(
    fence: DispatchContinuationFence,
) -> ContinuationResponsibilityProof:
    return ContinuationResponsibilityProof.no_proof(
        source_execution_decision_id=fence.execution_decision_id,
        reason_code="dispatch_continuation_no_responsibility_proof",
    )


def test_no_responsibility_proof_terminalizes_when_attempt_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-zombie",
        task_id="task-zombie",
        execution_decision_id="decision-zombie",
        work_unit_id="work-unit-zombie",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=10,
    )

    class Repository:
        def __init__(self) -> None:
            self.terminalized: list[tuple[DispatchContinuationFence, str]] = []
            self.deferred = False

        def terminalize_dispatch_continuation(self, claimed, *, error_code: str) -> bool:
            self.terminalized.append((claimed, error_code))
            return True

        def defer_dispatch_continuation(self, *_args, **_kwargs) -> bool:
            self.deferred = True
            return True

    monkeypatch.setattr(
        dispatch_service_module.settings,
        "research_dispatch_continuation_no_responsibility_proof_max_attempts",
        10,
    )
    repository = Repository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]

    assert (
        service._settle_processed_continuation(fence, proof=_no_responsibility_proof(fence))
        is True
    )
    assert repository.deferred is False
    assert len(repository.terminalized) == 1
    assert repository.terminalized[0][1] == (
        "dispatch_continuation_no_responsibility_proof_exhausted"
    )


def test_no_responsibility_proof_terminalizes_when_task_already_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-terminal-task",
        task_id="task-terminal-task",
        execution_decision_id="decision-terminal-task",
        work_unit_id="work-unit-terminal-task",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=2,
    )
    terminal_task = SimpleNamespace(
        status="failed",
        revision=12,
        execution_decisions=(
            SimpleNamespace(
                execution_decision_id="decision-terminal-task",
                status="completed",
            ),
        ),
    )

    class Repository:
        def __init__(self) -> None:
            self.terminalized: list[str] = []

        def load(self, **_kwargs):
            return terminal_task

        def terminalize_dispatch_continuation(self, _fence, *, error_code: str) -> bool:
            self.terminalized.append(error_code)
            return True

        def defer_dispatch_continuation(self, *_args, **_kwargs) -> bool:
            raise AssertionError("defer should not run for terminal tasks")

    monkeypatch.setattr(
        dispatch_service_module.settings,
        "research_dispatch_continuation_no_responsibility_proof_max_attempts",
        50,
    )
    repository = Repository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]

    assert (
        service._settle_processed_continuation(fence, proof=_no_responsibility_proof(fence))
        is True
    )
    assert repository.terminalized == [
        "dispatch_continuation_no_responsibility_proof_task_terminal"
    ]


def test_other_no_proof_reasons_still_defer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-defer",
        task_id="task-defer",
        execution_decision_id="decision-defer",
        work_unit_id="work-unit-defer",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=99,
    )
    proof = ContinuationResponsibilityProof.no_proof(
        source_execution_decision_id=fence.execution_decision_id,
        reason_code="dependency_release_not_applicable",
    )

    class Repository:
        def __init__(self) -> None:
            self.deferred: list[str] = []

        def defer_dispatch_continuation(self, _fence, *, error_code: str, **_kwargs) -> bool:
            self.deferred.append(error_code)
            return True

        def terminalize_dispatch_continuation(self, *_args, **_kwargs) -> bool:
            raise AssertionError("terminalize should not run")

    monkeypatch.setattr(
        dispatch_service_module.settings,
        "research_dispatch_retry_base_seconds",
        1,
    )
    monkeypatch.setattr(
        dispatch_service_module.settings,
        "research_dispatch_retry_max_seconds",
        1,
    )
    repository = Repository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]

    assert service._settle_processed_continuation(fence, proof=proof) is True
    assert repository.deferred == ["dependency_release_not_applicable"]

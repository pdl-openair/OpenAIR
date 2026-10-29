from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.continuation_responsibility import (
    ContinuationOwnerKind,
    ContinuationOwnerRef,
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
)
from app.modules.research_dispatch_service import ResearchDispatchService
from app.modules.research_orchestration_repository import (
    DispatchContinuationFence,
    ExecutionContinuationDirective,
)


def test_query_unsupported_unknown_emits_system_recovery_transfer_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-unknown",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unknown",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=2,
    )

    class Repository:
        def load(self, **_scope):
            return SimpleNamespace(revision=3, status=SimpleNamespace(value="running"))

        @staticmethod
        def system_external_recovery_state(_fence):
            return "unknown"

        def load_continuation_owner_for_work_unit(self, **kwargs):
            assert kwargs["work_unit_id"] == "work-unknown"
            return ContinuationOwnerRef(
                kind=ContinuationOwnerKind.DISPATCH,
                owner_id="dispatch-unknown",
                execution_decision_id="decision-1",
                work_unit_id="work-unknown",
            )

        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=3,
                next_instance_id=None,
                selection_request_id=None,
                reason_code=None,
            )

    class QueryUnsupportedRecovery:
        @staticmethod
        def capabilities():
            return SimpleNamespace(query_supported=False)

    service = ResearchDispatchService(
        Repository(),  # type: ignore[arg-type]
        external_recovery_service=QueryUnsupportedRecovery(),
    )
    service._release_ready_assignments_after_result = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: ContinuationResponsibilityProof.no_proof(
            source_execution_decision_id=fence.execution_decision_id,
            reason_code="dependency_release_not_applicable",
        )
    )
    monkeypatch.setattr(
        service, "_recover_confirmed_semantic_material_gap", lambda *_a, **_k: False
    )
    proof = service.process_continuation(fence)
    assert proof.kind is ContinuationResponsibilityKind.SYSTEM_RECOVERY
    assert proof.owner_id == "dispatch-unknown"

from __future__ import annotations

from dataclasses import replace
import inspect
import json
from threading import Event
from types import SimpleNamespace

import pytest

from app.execution.research_dispatch_provider import (
    DispatchProviderCapabilityError,
    DispatchSubmissionCompleted,
)
from app.modules import research_dispatch_service as dispatch_service_module
from app.modules.provider_registry import ProviderSubmissionSafety
from app.modules.continuation_responsibility import (
    ContinuationOwnerKind,
    ContinuationOwnerRef,
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
)
from app.modules.research_dispatch_service import (
    DispatchContinuationRetryableError,
    ResearchDispatchService,
)
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    initial_external_attempt_identity,
    DispatchContinuationFence,
    DispatchFence,
    ExecutionContinuationDirective,
)


SAFE = ProviderSubmissionSafety(True, True, True, False)
FENCE = DispatchFence(
    dispatch_id="dispatch-1",
    task_id="task-1",
    execution_decision_id="decision-1",
    work_unit_id="work-unit-1",
    idempotency_key="physical-key-1",
    lease_owner="worker-1",
    attempt_count=1,
)


def _test_completion_proof(
    fence: DispatchContinuationFence,
) -> ContinuationResponsibilityProof:
    return ContinuationResponsibilityProof(
        kind=ContinuationResponsibilityKind.TASK_TERMINAL,
        source_execution_decision_id=fence.execution_decision_id,
        reason_code="task_completed",
        target_type="task",
        target_id=fence.task_id,
        target_revision=1,
    )


def _test_no_proof(
    fence: DispatchContinuationFence,
) -> ContinuationResponsibilityProof:
    return ContinuationResponsibilityProof.no_proof(
        source_execution_decision_id=fence.execution_decision_id,
        reason_code="test_no_proof",
    )


class Repository:
    def __init__(self) -> None:
        self.authorize_calls = 0
        self.suppress_calls = 0

    def authorize_dispatch_submission(self, fence, **kwargs):
        self.authorize_calls += 1
        return AuthorizedDispatch(
            **fence.__dict__,
            external_attempt=initial_external_attempt_identity(fence),
            authorization_task_revision=kwargs["expected_task_revision"],
            work_unit_payload={"instruction": "frozen"},
            submission_mode=kwargs.get("submission_mode", "durable_idempotent"),
        )

    def mark_external_action_started(self, _fence):
        return True

    def suppress_dispatches(self, **kwargs):
        self.suppress_calls += 1
        return 1


def test_authorize_fails_before_repository_when_any_provider_safety_is_missing() -> None:
    repository = Repository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]

    with pytest.raises(DispatchProviderCapabilityError, match="provider_idempotency_unsupported"):
        service.authorize(
            FENCE,
            owner_id="owner-1",
            workspace_id="workspace-1",
            expected_task_revision=4,
            capabilities=replace(SAFE, acceptsIdempotencyKey=False),
        )

    assert repository.authorize_calls == 0


def test_authorize_passes_exact_scope_revision_and_fence_to_repository() -> None:
    repository = Repository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]

    authorized = service.authorize(
        FENCE,
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=4,
        capabilities=SAFE,
    )

    assert authorized is not None
    assert authorized.authorization_task_revision == 4
    assert authorized.idempotency_key == FENCE.idempotency_key
    assert repository.authorize_calls == 1
    assert authorized.submission_mode == "durable_idempotent"


def test_suppress_is_a_scoped_service_uow_boundary() -> None:
    repository = Repository()

    assert "expected_task_revision" in inspect.signature(ResearchDispatchService.suppress).parameters
    count = ResearchDispatchService(repository).suppress(  # type: ignore[arg-type]
        task_id="task-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=0,
    )

    assert count == 1
    assert repository.suppress_calls == 1


@pytest.mark.parametrize("revision", (-1, True, 1.5, "1"))
def test_suppress_rejects_non_strict_revision_before_repository(revision: object) -> None:
    repository = Repository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]

    with pytest.raises((TypeError, ValueError)):
        service.suppress(
            task_id="task-1",
            owner_id="owner-1",
            workspace_id="workspace-1",
            expected_task_revision=revision,  # type: ignore[arg-type]
        )

    assert repository.suppress_calls == 0


def test_outcome_rejects_plain_claim_fence_before_repository() -> None:
    class OutcomeRepository(Repository):
        def __init__(self) -> None:
            super().__init__()
            self.outcome_calls = 0

        def record_dispatch_outcome(self, *_args, **_kwargs):
            self.outcome_calls += 1
            return True

    repository = OutcomeRepository()

    with pytest.raises(TypeError, match="AuthorizedDispatch"):
        ResearchDispatchService(repository).record_outcome(  # type: ignore[arg-type]
            FENCE,
            owner_id="owner-1",
            workspace_id="workspace-1",
            outcome="accepted",
            remote_run_id="remote-1",
        )

    assert repository.outcome_calls == 0


def _records(**fields):
    return [SimpleNamespace(**fields)]


def _recovery_dispatch(*, revision=4, submission_mode="durable_idempotent"):
    return AuthorizedDispatch(
        **FENCE.__dict__,
        external_attempt=initial_external_attempt_identity(FENCE),
        authorization_task_revision=revision,
        work_unit_payload={"instruction": "frozen"},
        submission_mode=submission_mode,
    )


def _recovery_envelope(content):
    return {
        "schemaVersion": 1,
        "dispatchId": FENCE.dispatch_id,
        "title": "Validated result",
        "mimeType": "text/plain",
        "content": content,
        "providerId": "openai_primary",
        "adapter": "huiyan_chat_completions",
    }


def _bind_durable_blobs(monkeypatch, blobs):
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, storage_ref: blobs.get(storage_ref),
    )
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "store_durable_bytes",
        lambda _task_id, storage_ref, data, _content_type: (
            blobs.__setitem__(storage_ref, data)
            or SimpleNamespace(storage_type="local", storage_ref=storage_ref)
        ),
    )


def test_single_attempt_recovery_revalidates_durable_blob_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CompletedRepository(Repository):
        def __init__(self) -> None:
            super().__init__()
            self.completed_calls = []

        def record_dispatch_completed_result(self, fence, **kwargs):
            self.completed_calls.append((fence, kwargs))
            return True

        def load(self, **_scope):
            return SimpleNamespace(
                execution_decisions=_records(
                    execution_decision_id=FENCE.execution_decision_id,
                    purpose=SimpleNamespace(value="completion"),
                    assignments=[SimpleNamespace(assignment_id="assignment-1")],
                ),
                work_units=_records(
                    work_unit_id=FENCE.work_unit_id,
                    assignment_id="assignment-1",
                    execution_decision_id=FENCE.execution_decision_id,
                ),
            )

        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=5,
                next_instance_id=None,
                selection_request_id=None,
                reason_code=None,
            )

        def finalize_dispatch_completed_result(self, _fence, **_kwargs):
            return True

    authorized = _recovery_dispatch(submission_mode="single_attempt_no_replay")
    content = '{"result":"validated"}'
    envelope = {
        **_recovery_envelope(content),
        "artifactContract": "test_artifact_v1",
        "artifactSchemaDigest": "a" * 64,
    }
    blob = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    validation_calls: list[DispatchSubmissionCompleted] = []
    monkeypatch.setattr(dispatch_service_module.settings, "artifact_storage", "local")
    monkeypatch.setattr(
        dispatch_service_module,
        "validate_completed_dispatch_result",
        lambda _request, result, **_kwargs: (
            validation_calls.append(result)
            or SimpleNamespace(
                artifactContract="test_artifact_v1",
                artifactSchemaDigest="a" * 64,
                orchestrationPlan={"assignmentRole": "primary"},
            )
        ),
    )
    stored_blobs = {
        f"durable/research-dispatch/{FENCE.dispatch_id}/result.json": blob,
    }
    _bind_durable_blobs(monkeypatch, stored_blobs)
    repository = CompletedRepository()

    recovered = ResearchDispatchService(repository).recover_completed_result(  # type: ignore[arg-type]
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
    )

    assert recovered is True
    assert len(validation_calls) == 1
    assert validation_calls[0].content == content
    assert len(repository.completed_calls) == 1
    assert stored_blobs[
        f"durable/research-dispatch/{FENCE.dispatch_id}/artifact.json"
    ] == content.encode("utf-8")
    completed = repository.completed_calls[0][1]
    assert completed["storage_ref"].endswith("/result.json")
    assert completed["artifact_storage_ref"].endswith("/artifact.json")


def test_single_attempt_recovery_marks_only_the_internal_result_recovery_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorized = _recovery_dispatch(submission_mode="single_attempt_no_replay")
    envelope = _recovery_envelope('{"result":"validated"}')
    blob = json.dumps(envelope, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    monkeypatch.setattr(dispatch_service_module.settings, "artifact_storage", "local")
    monkeypatch.setattr(
        dispatch_service_module.artifact_write_service,
        "read_durable_bytes",
        lambda _task_id, _storage_type, _storage_ref: blob,
    )
    captured: dict[str, object] = {}

    class RecoveryOnlyService(ResearchDispatchService):
        def _record_completed_result(self, _fence, **kwargs):
            captured.update(kwargs)
            return True

    recovered = RecoveryOnlyService(Repository()).recover_completed_result(  # type: ignore[arg-type]
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
    )

    assert recovered is True
    assert captured["allow_closed_decision_for_result_recovery"] is True


def test_completed_producer_result_releases_frozen_dependency_before_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unit-producer",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=1,
    )
    producer_assignment = SimpleNamespace(
        assignment_id="assignment-producer", dependency_assignment_ids=[]
    )
    reviewer_assignment = SimpleNamespace(
        assignment_id="assignment-reviewer",
        dependency_assignment_ids=["assignment-producer"],
    )
    decision = SimpleNamespace(
        execution_decision_id="decision-1",
        status=SimpleNamespace(value="dispatched"),
        assignments=[producer_assignment, reviewer_assignment],
    )
    producer_work = SimpleNamespace(
        work_unit_id="work-unit-producer",
        assignment_id="assignment-producer",
        execution_decision_id="decision-1",
        source_mode=SimpleNamespace(value="live"),
        substage_instance_revision=1,
    )
    reviewer_work = SimpleNamespace(
        work_unit_id="work-unit-reviewer",
        assignment_id="assignment-reviewer",
        execution_decision_id="decision-1",
        source_mode=SimpleNamespace(value="live"),
        substage_instance_revision=1,
    )
    before = SimpleNamespace(
        revision=7, execution_decisions=[decision], work_units=[producer_work]
    )
    after = SimpleNamespace(
        revision=8, execution_decisions=[decision], work_units=[producer_work, reviewer_work]
    )

    class DependentRepository:
        def load(self, **_scope):
            return before

        def load_continuation_owner_for_work_unit(self, **kwargs):
            assert kwargs["execution_decision_id"] == "decision-1"
            assert kwargs["work_unit_id"] == "work-unit-reviewer"
            return ContinuationOwnerRef(
                kind=ContinuationOwnerKind.DISPATCH,
                owner_id="dispatch-reviewer",
                execution_decision_id="decision-1",
                work_unit_id="work-unit-reviewer",
            )

        def prepare_execution_continuation(self, **_kwargs):
            events.append("continuation")
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=8,
                next_instance_id=None,
                selection_request_id=None,
                reason_code=None,
            )

    class Coordinator:
        def release_ready(self, **kwargs):
            events.append("release")
            assert kwargs["expected_task_revision"] == 7
            return after

    service = ResearchDispatchService(
        DependentRepository(),  # type: ignore[arg-type]
        decision_coordinator=Coordinator(),
    )
    monkeypatch.setattr(
        service, "_recover_confirmed_semantic_material_gap", lambda *_args, **_kwargs: False
    )

    proof = service.process_continuation(fence)

    assert proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
    assert proof.successor_execution_decision_id == "decision-1"
    assert proof.decision_lineage == ("decision-1",)
    assert proof.successor_assignment_id == "assignment-reviewer"
    assert proof.successor_work_unit_id == "work-unit-reviewer"
    assert proof.owner_id == "dispatch-reviewer"
    assert events == ["release", "continuation"]


def test_release_ready_after_result_retries_on_concurrent_revision_bump() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unit-producer",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=1,
    )
    producer_assignment = SimpleNamespace(
        assignment_id="assignment-producer", dependency_assignment_ids=[]
    )
    reviewer_assignment = SimpleNamespace(
        assignment_id="assignment-reviewer",
        dependency_assignment_ids=["assignment-producer"],
    )
    decision = SimpleNamespace(
        execution_decision_id="decision-1",
        status=SimpleNamespace(value="dispatched"),
        assignments=[producer_assignment, reviewer_assignment],
    )
    producer_work = SimpleNamespace(
        work_unit_id="work-unit-producer",
        assignment_id="assignment-producer",
        execution_decision_id="decision-1",
        source_mode=SimpleNamespace(value="live"),
        substage_instance_revision=1,
    )
    reviewer_work = SimpleNamespace(
        work_unit_id="work-unit-reviewer",
        assignment_id="assignment-reviewer",
        execution_decision_id="decision-1",
        source_mode=SimpleNamespace(value="live"),
        substage_instance_revision=1,
    )
    before = SimpleNamespace(
        revision=7, execution_decisions=[decision], work_units=[producer_work]
    )
    bumped = SimpleNamespace(
        revision=8, execution_decisions=[decision], work_units=[producer_work]
    )
    after = SimpleNamespace(
        revision=9, execution_decisions=[decision], work_units=[producer_work, reviewer_work]
    )
    load_calls = {"count": 0}

    class DependentRepository:
        def load(self, **_scope):
            load_calls["count"] += 1
            return bumped if load_calls["count"] > 1 else before

        def load_continuation_owner_for_work_unit(self, **_kwargs):
            return ContinuationOwnerRef(
                kind=ContinuationOwnerKind.DISPATCH,
                owner_id="dispatch-reviewer",
                execution_decision_id="decision-1",
                work_unit_id="work-unit-reviewer",
            )

        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="none",
                expected_task_revision=9,
                next_instance_id=None,
                selection_request_id=None,
                reason_code=None,
            )

    release_calls: list[int] = []

    class Coordinator:
        def release_ready(self, **kwargs):
            release_calls.append(int(kwargs["expected_task_revision"]))
            if len(release_calls) == 1:
                from app.modules.research_execution_decision_service import (
                    ExecutionPreflightBlocked,
                )

                raise ExecutionPreflightBlocked("task_revision_conflict")
            assert kwargs["expected_task_revision"] == 8
            return after

    service = ResearchDispatchService(
        DependentRepository(),  # type: ignore[arg-type]
        decision_coordinator=Coordinator(),
    )

    proof = service._release_ready_assignments_after_result(
        fence,
        owner_id="owner-1",
        workspace_id="workspace-1",
    )

    assert proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
    assert release_calls == [7, 8]
    assert load_calls["count"] == 2


def test_successor_proof_follows_exact_continuation_lineage_after_retry() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-source",
        task_id="task-lineage",
        execution_decision_id="decision-source",
        work_unit_id="work-source",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=2,
    )
    source_assignment = SimpleNamespace(
        assignment_id="assignment-source", dependency_assignment_ids=[]
    )
    middle_assignment = SimpleNamespace(
        assignment_id="assignment-middle", dependency_assignment_ids=[]
    )
    final_assignment = SimpleNamespace(
        assignment_id="assignment-final", dependency_assignment_ids=[]
    )
    decisions = [
        SimpleNamespace(
            execution_decision_id="decision-source",
            selection_request_id="initial:source",
            decision_sequence=1,
            status=SimpleNamespace(value="closed"),
            assignments=[source_assignment],
        ),
        SimpleNamespace(
            execution_decision_id="decision-middle",
            selection_request_id="continuation:decision-source",
            decision_sequence=2,
            status=SimpleNamespace(value="closed"),
            assignments=[middle_assignment],
        ),
        SimpleNamespace(
            execution_decision_id="decision-final",
            selection_request_id="continuation:decision-middle",
            decision_sequence=3,
            status=SimpleNamespace(value="dispatched"),
            assignments=[final_assignment],
        ),
    ]
    work_units = [
        SimpleNamespace(
            work_unit_id="work-source",
            assignment_id="assignment-source",
            execution_decision_id="decision-source",
            source_mode=SimpleNamespace(value="live"),
            substage_instance_revision=1,
        ),
        SimpleNamespace(
            work_unit_id="work-middle",
            assignment_id="assignment-middle",
            execution_decision_id="decision-middle",
            source_mode=SimpleNamespace(value="live"),
            substage_instance_revision=1,
        ),
        SimpleNamespace(
            work_unit_id="work-final",
            assignment_id="assignment-final",
            execution_decision_id="decision-final",
            source_mode=SimpleNamespace(value="live"),
            substage_instance_revision=1,
        ),
    ]
    task = SimpleNamespace(execution_decisions=decisions, work_units=work_units)

    class LineageRepository:
        def load_continuation_owner_for_work_unit(self, **kwargs):
            if kwargs["work_unit_id"] != "work-final":
                return None
            return ContinuationOwnerRef(
                kind=ContinuationOwnerKind.DISPATCH,
                owner_id="dispatch-final",
                execution_decision_id="decision-final",
                work_unit_id="work-final",
            )

    proof = ResearchDispatchService(
        LineageRepository()  # type: ignore[arg-type]
    )._successor_execution_proof_from_task(
        fence,
        task=task,
        successor_execution_decision_id="decision-middle",
        selection_request_id="continuation:decision-source",
    )

    assert proof.kind is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
    assert proof.decision_lineage == (
        "decision-source",
        "decision-middle",
        "decision-final",
    )
    assert proof.successor_execution_decision_id == "decision-final"
    assert proof.successor_work_unit_id == "work-final"
    assert proof.owner_id == "dispatch-final"


def test_committed_semantic_gap_recovers_from_durable_facts_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery CAS is outside result persistence and binds the pinned snapshot."""

    class Repository:
        def __init__(self) -> None:
            design_ref = SimpleNamespace(research_design_id="design", version=1)
            readiness = {"explicit": True, "downstreamReady": False}
            self.task = SimpleNamespace(
                revision=17,
                execution_mode="automatic_research",
                active_research_design_ref=design_ref,
                research_design_versions=_records(**design_ref.__dict__, status="confirmed"),
                execution_decisions=_records(
                    execution_decision_id=FENCE.execution_decision_id,
                    purpose="completion",
                    status="closed",
                    outcome="blocked",
                    research_design_version=1,
                    substage_instance_id="blocked",
                    substage_instance_revision=1,
                ),
                substage_runtime_states=_records(
                    substage_instance_id="blocked",
                    instance_revision=1,
                    status="skipped",
                    status_reason_code="semantic_material_insufficient",
                ),
                work_units=_records(
                    work_unit_id=FENCE.work_unit_id,
                    execution_decision_id=FENCE.execution_decision_id,
                    status="blocked",
                    terminal_reason_code="semantic_material_insufficient",
                ),
                artifacts=_records(
                    work_unit_id=FENCE.work_unit_id,
                    artifact_state="insufficient_materials",
                    validator_result={"semanticReadiness": readiness},
                ),
                registry_snapshot_ref=SimpleNamespace(
                    model_dump=lambda **_kwargs: {"schemaVersion": 1, "contentHash": "pinned"},
                ),
            )

        def load(self, **_scope):
            return self.task

    calls: list[dict] = []

    class Source:
        def __init__(self, *, snapshot_ref):
            assert snapshot_ref == {"schemaVersion": 1, "contentHash": "pinned"}

    class RecoveryService:
        def __init__(self, *, repository, definition_source):
            assert isinstance(definition_source, Source)
            assert repository is repository_instance

        def recover_confirmed_semantic_material_gap(self, **kwargs):
            calls.append(kwargs)

    from app.modules import production_definition_source, research_design_service

    monkeypatch.setattr(production_definition_source, "ProductionDefinitionSource", Source)
    monkeypatch.setattr(research_design_service, "ResearchDesignService", RecoveryService)
    repository_instance = Repository()

    service = ResearchDispatchService(repository_instance)  # type: ignore[arg-type]
    recovered = service._recover_confirmed_semantic_material_gap(
        _recovery_dispatch(revision=16), owner_id="owner-1", workspace_id="workspace-1"
    )
    assert recovered is True
    assert calls == [
        {
            "task_id": "task-1",
            "owner_id": "owner-1",
            "workspace_id": "workspace-1",
            "blocked_instance_id": "blocked",
            "gap_reason_code": "semantic_material_insufficient",
            "expected_task_revision": 17,
        }
    ]


def test_continuation_failure_defers_only_the_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unit-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=3,
    )

    class ContinuationRepository:
        def __init__(self) -> None:
            self.deferred: list[tuple[DispatchContinuationFence, dict]] = []
            self.completed = False

        def claim_dispatch_continuation(self, **_kwargs):
            return fence

        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="start",
                expected_task_revision=8,
                next_instance_id="sub-next",
                selection_request_id=(
                    "continuation:decision-1:sub-next:"
                    "execution-policy.v2.automatic_research"
                ),
                reason_code=None,
            )

        def defer_dispatch_continuation(self, deferred_fence, **kwargs):
            self.deferred.append((deferred_fence, kwargs))
            return True

        def complete_dispatch_continuation(self, _fence, **_kwargs):
            self.completed = True
            return True

    class Starter:
        def start(self, **_kwargs):
            raise DispatchContinuationRetryableError("temporary selection failure")

    monkeypatch.setattr(
        dispatch_service_module.settings,
        "research_dispatch_retry_base_seconds",
        2,
    )
    monkeypatch.setattr(
        dispatch_service_module.settings,
        "research_dispatch_retry_max_seconds",
        30,
    )
    repository = ContinuationRepository()

    assert ResearchDispatchService(
        repository,  # type: ignore[arg-type]
        decision_coordinator=Starter(),
    ).process_next_dispatch_continuation(
        lease_owner="worker-1",
        lease_seconds=30,
    ) is True

    assert repository.completed is False
    assert repository.deferred == [
        (
            fence,
            {
                "error_code": "temporary selection failure",
                "retry_delay_seconds": 8,
            },
        )
    ]


def test_unclassified_continuation_failure_retains_recovery_owner_at_error_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-terminal",
        task_id="task-terminal",
        execution_decision_id="decision-terminal",
        work_unit_id="work-unit-terminal",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=3,
    )

    class ContinuationRepository:
        def __init__(self) -> None:
            self.failed: list[tuple[DispatchContinuationFence, dict]] = []
            self.deferred = False

        def claim_dispatch_continuation(self, **_kwargs):
            return fence

        def renew_dispatch_continuation(self, _fence, *, lease_seconds):
            assert lease_seconds == 30
            return True

        def fail_dispatch_continuation(self, failed_fence, **kwargs):
            self.failed.append((failed_fence, kwargs))
            return True

        def defer_dispatch_continuation(self, _fence, **_kwargs):
            self.deferred = True
            return True

    monkeypatch.setattr(
        dispatch_service_module.settings,
        "research_dispatch_continuation_internal_error_max_attempts",
        3,
    )
    repository = ContinuationRepository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]
    service.process_continuation = lambda _fence: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("stable invariant failure")
    )

    assert service.process_next_dispatch_continuation(
        lease_owner="worker-1",
        lease_seconds=30,
    ) is True
    assert repository.deferred is False
    assert len(repository.failed) == 1
    failed_fence, failed_kwargs = repository.failed[0]
    assert failed_fence == fence
    assert failed_kwargs["error_code"] == "dispatch_continuation_internal_error_exhausted"
    assert failed_kwargs["retry_delay_seconds"] >= 1


def test_unclassified_coordinator_failure_is_not_misclassified_as_retryable() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-stable-coordinator",
        task_id="task-stable-coordinator",
        execution_decision_id="decision-stable-coordinator",
        work_unit_id="work-unit-stable-coordinator",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=1,
    )

    class ContinuationRepository:
        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="start",
                expected_task_revision=8,
                next_instance_id="sub-next",
                selection_request_id=(
                    "continuation:decision-stable-coordinator:sub-next:"
                    "execution-policy.v2.automatic_research"
                ),
                reason_code=None,
            )

    class Starter:
        def start(self, **_kwargs):
            raise RuntimeError("stable coordinator invariant failure")

    service = ResearchDispatchService(
        ContinuationRepository(),  # type: ignore[arg-type]
        decision_coordinator=Starter(),
    )

    with pytest.raises(
        RuntimeError,
        match="stable coordinator invariant failure",
    ):
        service.process_continuation(fence)


def test_long_continuation_renews_lease_and_settles_without_provider_submission() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-long",
        task_id="task-long",
        execution_decision_id="decision-long",
        work_unit_id="work-unit-long",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=1,
    )

    class ContinuationRepository:
        def __init__(self) -> None:
            self.renew_calls = 0
            self.completed = 0
            self.periodic_renewed = Event()

        def claim_dispatch_continuation(self, **_kwargs):
            return fence

        def renew_dispatch_continuation(self, renewed_fence, *, lease_seconds):
            assert renewed_fence == fence
            assert lease_seconds == 1
            self.renew_calls += 1
            if self.renew_calls >= 5:
                self.periodic_renewed.set()
            return True

        def complete_dispatch_continuation(self, completed_fence, **_kwargs):
            assert completed_fence == fence
            self.completed += 1
            return True

    repository = ContinuationRepository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]
    def process_continuation(processed_fence):
        assert processed_fence == fence
        assert repository.periodic_renewed.wait(timeout=2)
        return _test_completion_proof(fence)

    service.process_continuation = process_continuation  # type: ignore[method-assign]

    assert service.process_next_dispatch_continuation(
        lease_owner="worker-1",
        lease_seconds=1,
    ) is True
    assert repository.renew_calls >= 5
    assert repository.completed == 1


@pytest.mark.parametrize("completed", [True, False])
def test_continuation_settlement_cas_failure_is_not_reported_as_success(
    completed: bool,
) -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-cas",
        task_id="task-cas",
        execution_decision_id="decision-cas",
        work_unit_id="work-unit-cas",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="worker-1",
        attempt_count=2,
    )

    class ContinuationRepository:
        def claim_dispatch_continuation(self, **_kwargs):
            return fence

        def renew_dispatch_continuation(self, _fence, *, lease_seconds):
            assert lease_seconds == 30
            return True

        def complete_dispatch_continuation(self, _fence, **_kwargs):
            return False

        def defer_dispatch_continuation(self, _fence, **_kwargs):
            return False

    service = ResearchDispatchService(ContinuationRepository())  # type: ignore[arg-type]
    service.process_continuation = (  # type: ignore[method-assign]
        (lambda current: _test_completion_proof(current))
        if completed
        else (lambda current: _test_no_proof(current))
    )

    assert service.process_next_dispatch_continuation(
        lease_owner="worker-1",
        lease_seconds=30,
    ) is False


def test_lost_continuation_fence_cannot_process_or_settle() -> None:
    fence = DispatchContinuationFence(
        dispatch_id="dispatch-stale",
        task_id="task-stale",
        execution_decision_id="decision-stale",
        work_unit_id="work-unit-stale",
        owner_id="owner-1",
        workspace_id="workspace-1",
        lease_owner="old-worker",
        attempt_count=1,
    )

    class ContinuationRepository:
        def __init__(self) -> None:
            self.completed = False

        def claim_dispatch_continuation(self, **_kwargs):
            return fence

        def renew_dispatch_continuation(self, _fence, *, lease_seconds):
            assert lease_seconds == 30
            return False

        def complete_dispatch_continuation(self, _fence, **_kwargs):
            self.completed = True
            return True

    repository = ContinuationRepository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]
    processed = False

    def process_continuation(_fence):
        nonlocal processed
        processed = True
        return _test_completion_proof(fence)

    service.process_continuation = process_continuation  # type: ignore[method-assign]

    assert service.process_next_dispatch_continuation(
        lease_owner="old-worker",
        lease_seconds=30,
    ) is False
    assert processed is False
    assert repository.completed is False


def test_runtime_blocker_is_durably_paused_before_result_dispatch_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.research_execution_decision_service import ExecutionPreflightBlocked

    class ContinuationRepository(Repository):
        def __init__(self) -> None:
            super().__init__()
            self.blocked_calls = []
            self.finalized = False

        def record_dispatch_completed_result(self, _fence, **_kwargs):
            return True

        def prepare_execution_continuation(self, **_kwargs):
            return ExecutionContinuationDirective(
                action="start",
                expected_task_revision=8,
                next_instance_id="sub-next",
                selection_request_id=(
                    "continuation:decision-1:sub-next:execution-policy.v2.automatic_research"
                ),
                reason_code=None,
            )

        def record_execution_continuation_blocked(self, **kwargs):
            self.blocked_calls.append(kwargs)
            return 9

        def finalize_dispatch_completed_result(self, _fence, **_kwargs):
            self.finalized = True
            return True

    class Starter:
        def start(self, **_kwargs):
            raise ExecutionPreflightBlocked("runtime_unavailable")

    authorized = _recovery_dispatch()
    monkeypatch.setattr(
        dispatch_service_module,
        "validate_completed_dispatch_result",
        lambda *_args, **_kwargs: SimpleNamespace(
            artifactContract="test_artifact_v1",
            artifactSchemaDigest="a" * 64,
            orchestrationPlan={"assignmentRole": "reviewer"},
        ),
    )
    blobs = {}
    _bind_durable_blobs(monkeypatch, blobs)
    repository = ContinuationRepository()

    service = ResearchDispatchService(
        repository,
        decision_coordinator=Starter(),
    )
    recorded = service.record_completed_result(
        authorized,
        owner_id="owner-1",
        workspace_id="workspace-1",
        result=DispatchSubmissionCompleted(
            title="Reviewed",
            mime_type="application/json",
            content='{"verdict":"usable"}',
            provider_id="openai_primary",
            adapter="huiyan_chat_completions",
        ),
    )

    assert recorded is True
    assert repository.blocked_calls == []
    proof = service.process_continuation(
        DispatchContinuationFence(
            dispatch_id=authorized.dispatch_id,
            task_id=authorized.task_id,
            execution_decision_id=authorized.execution_decision_id,
            work_unit_id=authorized.work_unit_id,
            owner_id="owner-1",
            workspace_id="workspace-1",
            lease_owner="worker-1",
            attempt_count=1,
        )
    )
    assert proof.kind is ContinuationResponsibilityKind.NO_PROOF
    assert repository.blocked_calls == [
        {
            "task_id": "task-1",
            "owner_id": "owner-1",
            "workspace_id": "workspace-1",
            "expected_task_revision": 8,
            "next_instance_id": "sub-next",
            "reason_code": "runtime_unavailable",
        }
    ]


def test_retry_policy_is_bounded_and_passed_to_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RetryRepository(Repository):
        def __init__(self) -> None:
            super().__init__()
            self.retry_calls: list[tuple[object, dict]] = []

        def record_dispatch_retryable_failure(self, fence, **kwargs):
            self.retry_calls.append((fence, kwargs))
            return "retry_scheduled"

    repository = RetryRepository()
    service = ResearchDispatchService(repository)  # type: ignore[arg-type]
    monkeypatch.setattr(dispatch_service_module.settings, "research_dispatch_max_attempts", 5)
    monkeypatch.setattr(dispatch_service_module.settings, "research_dispatch_retry_base_seconds", 2)
    monkeypatch.setattr(dispatch_service_module.settings, "research_dispatch_retry_max_seconds", 5)
    fence = replace(FENCE, attempt_count=4)

    assert service.defer_unsubmitted(
        fence,
        owner_id="owner-1",
        workspace_id="workspace-1",
        error_code="provider_temporarily_unavailable",
    ) == "retry_scheduled"

    _, call = repository.retry_calls[0]
    assert call["max_attempts"] == 5
    assert call["retry_delay_seconds"] == 5
    assert call["error_code"] == "provider_temporarily_unavailable"

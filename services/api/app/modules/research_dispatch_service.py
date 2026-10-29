"""Application boundary for fenced research dispatch state transitions."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from app.config import settings
from app.execution.external_source_identity import validate_result_external_sources
from app.execution.research_dispatch_context import submission_request_from_authorized
from app.execution.research_dispatch_huiyan_provider import validate_completed_dispatch_result
from app.execution.research_dispatch_provider import (
    SKILL_FINALIZATION_RUNTIME_FAILED,
    DispatchSubmissionCompleted,
    DispatchSubmissionRequest,
    require_safe_submission,
)
from app.modules.artifact_write import artifact_write_service
from app.modules.continuation_responsibility import (
    NEEDS_INPUT_RESUME_PATH,
    NEEDS_INPUT_USER_ACTION,
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
    user_responsibility_contract,
)
from app.modules.provider_registry import ProviderSubmissionSafety
from app.modules.research_blocker_semantics import needs_user_input
from app.modules.research_dispatch_continuation import DispatchContinuationLeaseKeeper
from app.modules.research_external_recovery_persistence import prepare_continuation_preconditions
from app.modules.research_orchestration_repository import (
    AuthorizedDispatch,
    DispatchContinuationFence,
    DispatchFence,
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
    TaskRevisionConflictError,
)


class DispatchContinuationRetryableError(RuntimeError):
    pass


_DEPENDENT_ASSIGNMENT_RELEASE_REVISION_ATTEMPTS = 8
_DISPATCH_CONTINUATION_NO_RESPONSIBILITY_PROOF = (
    "dispatch_continuation_no_responsibility_proof"
)
_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


class ExecutionDecisionCoordinator(Protocol):
    def start(self, **kwargs: Any): ...

    def start_next(self, **kwargs: Any): ...

    def release_ready(self, **kwargs: Any): ...


class _RepositorySnapshotArchive:
    def __init__(self, repository: ResearchOrchestrationRepository) -> None:
        self._repository = repository

    def get(self, content_hash: str):
        from app.modules.registry_snapshot_repository import snapshot_from_envelope

        return snapshot_from_envelope(
            self._repository.load_registry_snapshot(content_hash)
        )

    def put(self, _snapshot):
        raise RuntimeError("dispatch continuation cannot mutate Registry snapshots")


def _build_execution_decision_coordinator(
    repository: ResearchOrchestrationRepository,
) -> ExecutionDecisionCoordinator:
    from app.modules.research_execution_decision_service import (
        ResearchExecutionDecisionService,
        build_production_resolution_facts,
    )
    from app.modules.research_orchestration_resolver import ResearchOrchestrationResolver

    return ResearchExecutionDecisionService(
        repository=repository,
        resolver=ResearchOrchestrationResolver(_RepositorySnapshotArchive(repository)),
        facts_source=build_production_resolution_facts,
    )


class ResearchDispatchService:
    def __init__(
        self,
        repository: ResearchOrchestrationRepository | None = None,
        *,
        decision_coordinator: ExecutionDecisionCoordinator | None = None,
        controller_command_repository: Any | None = None,
        external_recovery_service: Any | None = None,
    ) -> None:
        self._repository = repository or ResearchOrchestrationRepository()
        self._decision_coordinator = decision_coordinator
        self._controller_command_repository = controller_command_repository
        self._external_recovery_service = external_recovery_service

    def _maybe_auto_system_explicit_retry(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str | None,
    ) -> None:
        normalized = str(error_code or "").strip()
        if normalized != SKILL_FINALIZATION_RUNTIME_FAILED:
            return
        builder = getattr(self._repository, "prepare_system_explicit_retry_command", None)
        if not callable(builder):
            return
        command = builder(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=normalized,
        )
        if command is None:
            return
        service = self._external_recovery_service
        if service is None:
            from app.modules.research_external_recovery_service import (
                production_external_recovery_service,
            )

            service = production_external_recovery_service(self._repository)
        try:
            service.retry(command)
        except Exception:
            logging.getLogger("openair").exception(
                "system explicit retry failed dispatch=%s task=%s workUnit=%s",
                fence.dispatch_id,
                fence.task_id,
                fence.work_unit_id,
            )

    def _system_reconcile_external_result(
        self, fence: DispatchContinuationFence,
    ) -> bool:
        """Advance, and retain ownership of, an external recovery obligation."""
        state_reader = getattr(
            self._repository, "system_external_recovery_state", None
        )
        if not callable(state_reader) or state_reader(fence) is None:
            return False
        service = self._external_recovery_service
        if service is None:
            from app.modules.research_external_recovery_service import (
                production_external_recovery_service,
            )

            service = production_external_recovery_service(self._repository)
        if not service.capabilities().query_supported:
            loader = getattr(
                self._repository, "load_providerless_recovery_authorization", None
            )
            if callable(loader):
                authorized = loader(fence)
                if authorized is not None:
                    try:
                        if self.recover_completed_result(
                            authorized,
                            owner_id=fence.owner_id,
                            workspace_id=fence.workspace_id,
                        ):
                            return False
                    except DispatchContinuationRetryableError:
                        return True
            retry_builder = getattr(self._repository, "prepare_system_explicit_retry_command", None)
            if callable(retry_builder):
                command = retry_builder(
                    fence, owner_id=fence.owner_id, workspace_id=fence.workspace_id,
                    error_code="",
                )
                if command is not None:
                    service.retry(command)
            return True
        command_builder = getattr(
            self._repository, "prepare_system_reconcile_command", None
        )
        if not callable(command_builder):
            raise DispatchContinuationRetryableError(
                "system_reconciliation_command_builder_missing"
            )
        command = command_builder(fence)
        if command is None:
            return True
        service.reconcile(command)
        return True

    def _coordinator(self) -> ExecutionDecisionCoordinator:
        if self._decision_coordinator is None:
            self._decision_coordinator = _build_execution_decision_coordinator(
                self._repository
            )
        return self._decision_coordinator

    @staticmethod
    def _no_continuation_proof(
        fence: DispatchContinuationFence,
        reason_code: str,
    ) -> ContinuationResponsibilityProof:
        return ContinuationResponsibilityProof.no_proof(
            source_execution_decision_id=fence.execution_decision_id,
            reason_code=reason_code,
        )

    def _load_continuation_task(self, fence: DispatchContinuationFence):
        loader = getattr(self._repository, "load", None)
        if not callable(loader):
            return None
        return loader(
            task_id=fence.task_id,
            owner_id=fence.owner_id,
            workspace_id=fence.workspace_id,
        )

    def _task_terminal_proof(
        self,
        fence: DispatchContinuationFence,
        task: Any,
    ) -> ContinuationResponsibilityProof:
        status = self._value(task, "status")
        if status not in {"completed", "failed", "cancelled"}:
            return self._no_continuation_proof(fence, "task_not_terminal")
        return ContinuationResponsibilityProof(
            kind=ContinuationResponsibilityKind.TASK_TERMINAL,
            source_execution_decision_id=fence.execution_decision_id,
            reason_code=f"task_{status}",
            target_type="task",
            target_id=fence.task_id,
            target_revision=int(task.revision),
        )

    def _user_stopped_proof(
        self,
        fence: DispatchContinuationFence,
    ) -> ContinuationResponsibilityProof:
        task = self._load_continuation_task(fence)
        if task is None or not self._execution_stop_active(fence):
            return self._no_continuation_proof(fence, "user_stop_not_durable")
        return ContinuationResponsibilityProof(
            kind=ContinuationResponsibilityKind.USER_STOPPED,
            source_execution_decision_id=fence.execution_decision_id,
            reason_code="user_stop_requested",
            target_type="task",
            target_id=fence.task_id,
            target_revision=int(task.revision),
        )

    def _user_responsibility_proof(
        self,
        fence: DispatchContinuationFence,
        *,
        reason_code: str,
        target_instance_id: str | None = None,
        selection_request_id: str | None = None,
    ) -> ContinuationResponsibilityProof:
        task = self._load_continuation_task(fence)
        if task is None:
            return self._no_continuation_proof(fence, "user_responsibility_task_missing")
        if reason_code == "research_design_confirmation_required":
            drafts = sorted(
                (
                    row for row in getattr(task, "research_design_versions", ())
                    if self._value(row, "status") == "draft"
                ),
                key=lambda row: int(row.version),
                reverse=True,
            )
            if not drafts:
                return self._no_continuation_proof(
                    fence, "research_design_confirmation_target_missing"
                )
            target = drafts[0]
            action_resume = user_responsibility_contract(reason_code)
            if action_resume is None:
                return self._no_continuation_proof(
                    fence, "research_design_confirmation_contract_missing"
                )
            return ContinuationResponsibilityProof(
                kind=ContinuationResponsibilityKind.USER_RESPONSIBILITY,
                source_execution_decision_id=fence.execution_decision_id,
                reason_code=reason_code,
                target_type="research_design",
                target_id=str(target.research_design_id),
                target_revision=int(target.version),
                user_action=action_resume[0],
                resume_path=action_resume[1],
            )

        runtimes = [
            row
            for row in getattr(task, "substage_runtime_states", ())
            if row.status_reason_code == reason_code
            and (target_instance_id is None or row.substage_instance_id == target_instance_id)
        ]
        if reason_code in {
            "stage_confirmation_required",
            "manual_step_confirmation_required",
            "reviewer_human_gate_required",
        }:
            runtimes = [row for row in runtimes if self._value(row, "status") == "ready"]
            action_resume = user_responsibility_contract(reason_code)
            if not runtimes or action_resume is None:
                return self._no_continuation_proof(
                    fence, "explicit_user_gate_target_missing"
                )
            target = sorted(
                runtimes,
                key=lambda row: (row.substage_instance_id, int(row.instance_revision)),
            )[0]
            return ContinuationResponsibilityProof(
                kind=ContinuationResponsibilityKind.USER_RESPONSIBILITY,
                source_execution_decision_id=fence.execution_decision_id,
                reason_code=reason_code,
                target_type="substage_instance",
                target_id=str(target.substage_instance_id),
                target_revision=int(target.instance_revision),
                user_action=action_resume[0],
                resume_path=action_resume[1],
            )

        if not needs_user_input(reason_code) or not selection_request_id:
            return self._no_continuation_proof(fence, "user_owned_reason_not_proven")
        runtimes = [row for row in runtimes if self._value(row, "status") == "needs_input"]
        if not runtimes:
            return self._no_continuation_proof(fence, "needs_input_target_missing")
        target = sorted(
            runtimes,
            key=lambda row: (row.substage_instance_id, int(row.instance_revision)),
        )[0]
        return ContinuationResponsibilityProof(
            kind=ContinuationResponsibilityKind.USER_RESPONSIBILITY,
            source_execution_decision_id=fence.execution_decision_id,
            reason_code=reason_code,
            target_type="substage_instance",
            target_id=str(target.substage_instance_id),
            target_revision=int(target.instance_revision),
            user_action=NEEDS_INPUT_USER_ACTION,
            resume_path=NEEDS_INPUT_RESUME_PATH,
            selection_request_id=selection_request_id,
        )

    def _blocked_selection_user_proof(
        self,
        fence: DispatchContinuationFence,
        *,
        selection_request_id: str,
        reason_code: str,
    ) -> ContinuationResponsibilityProof:
        if not needs_user_input(reason_code):
            return self._no_continuation_proof(fence, "system_blocker_not_user_owned")
        evidence_reader = getattr(self._repository, "lookup_selection_replay_evidence", None)
        if not callable(evidence_reader):
            return self._no_continuation_proof(fence, "needs_input_evidence_reader_missing")
        evidence = evidence_reader(
            task_id=fence.task_id,
            owner_id=fence.owner_id,
            workspace_id=fence.workspace_id,
            selection_request_id=selection_request_id,
        )
        instance_id = None if evidence is None else getattr(evidence, "instance_id", None)
        if not instance_id:
            return self._no_continuation_proof(fence, "needs_input_identity_missing")
        return self._user_responsibility_proof(
            fence,
            reason_code=reason_code,
            target_instance_id=str(instance_id),
            selection_request_id=selection_request_id,
        )

    def _successor_execution_proof_from_task(
        self,
        fence: DispatchContinuationFence,
        *,
        task: Any,
        successor_execution_decision_id: str,
        selection_request_id: str | None,
        candidate_work_unit_ids: set[str] | None = None,
    ) -> ContinuationResponsibilityProof:
        """Prove an exact successor owner, following only deterministic lineage.

        A committed Selection Request is replay/idempotency evidence, not the proof.
        When a retry happens after the immediate successor has already progressed,
        this follows only ``continuation:<previous-decision>`` links until it finds
        an exact live WorkUnit with an active/recoverable durable owner.
        """
        owner_reader = getattr(
            self._repository, "load_continuation_owner_for_work_unit", None
        )
        if not callable(owner_reader):
            return self._no_continuation_proof(fence, "successor_owner_reader_missing")
        source_work = next(
            (
                row for row in getattr(task, "work_units", ())
                if row.work_unit_id == fence.work_unit_id
                and row.execution_decision_id == fence.execution_decision_id
            ),
            None,
        )
        decisions = {
            str(row.execution_decision_id): row
            for row in getattr(task, "execution_decisions", ())
        }
        if source_work is None or successor_execution_decision_id not in decisions:
            return self._no_continuation_proof(fence, "successor_lineage_missing")

        lineage = [fence.execution_decision_id]
        if successor_execution_decision_id != fence.execution_decision_id:
            lineage.append(successor_execution_decision_id)
        visited = set(lineage)
        current_decision_id = successor_execution_decision_id
        first_request_id = selection_request_id

        while True:
            decision = decisions.get(current_decision_id)
            if decision is None:
                return self._no_continuation_proof(fence, "successor_lineage_missing")
            assignment_by_id = {row.assignment_id: row for row in decision.assignments}
            candidates = sorted(
                (
                    row for row in getattr(task, "work_units", ())
                    if row.execution_decision_id == current_decision_id
                    and self._value(row, "source_mode") == "live"
                    and row.work_unit_id != fence.work_unit_id
                    and (
                        candidate_work_unit_ids is None
                        or current_decision_id != fence.execution_decision_id
                        or row.work_unit_id in candidate_work_unit_ids
                    )
                ),
                key=lambda row: row.work_unit_id,
            )
            for work in candidates:
                assignment = assignment_by_id.get(work.assignment_id)
                if assignment is None:
                    continue
                released_by_dependency = (
                    candidate_work_unit_ids is not None
                    and current_decision_id == fence.execution_decision_id
                    and str(work.work_unit_id) in candidate_work_unit_ids
                )
                if (
                    len(lineage) == 1
                    and not released_by_dependency
                    and source_work.assignment_id
                    not in assignment.dependency_assignment_ids
                ):
                    continue
                owner = owner_reader(
                    task_id=fence.task_id,
                    owner_id=fence.owner_id,
                    workspace_id=fence.workspace_id,
                    execution_decision_id=current_decision_id,
                    work_unit_id=work.work_unit_id,
                )
                if owner is None:
                    continue
                return ContinuationResponsibilityProof(
                    kind=ContinuationResponsibilityKind.SUCCESSOR_EXECUTION,
                    source_execution_decision_id=fence.execution_decision_id,
                    reason_code=(
                        "same_decision_dependency_released"
                        if len(lineage) == 1
                        else "successor_execution_started"
                    ),
                    target_type="work_unit",
                    target_id=str(work.work_unit_id),
                    target_revision=int(work.substage_instance_revision),
                    selection_request_id=first_request_id,
                    decision_lineage=tuple(lineage),
                    successor_execution_decision_id=current_decision_id,
                    successor_assignment_id=str(work.assignment_id),
                    successor_work_unit_id=str(work.work_unit_id),
                    owner_kind=owner.kind,
                    owner_id=owner.owner_id,
                )

            # Same-Decision dependency release is deliberately one hop: no
            # Selection Request exists yet, so widening here would invent lineage.
            if len(lineage) == 1:
                break
            next_request_id = f"continuation:{current_decision_id}"
            next_decisions = sorted(
                (
                    row for row in decisions.values()
                    if str(row.selection_request_id) == next_request_id
                ),
                key=lambda row: int(row.decision_sequence),
            )
            if len(next_decisions) != 1:
                break
            next_decision_id = str(next_decisions[0].execution_decision_id)
            if next_decision_id in visited:
                break
            visited.add(next_decision_id)
            lineage.append(next_decision_id)
            current_decision_id = next_decision_id
            candidate_work_unit_ids = None

        return self._no_continuation_proof(fence, "successor_durable_owner_missing")

    def _successor_execution_proof_from_commit(
        self,
        fence: DispatchContinuationFence,
        *,
        commit: Any,
        selection_request_id: str,
    ) -> ContinuationResponsibilityProof:
        task = getattr(commit, "task", None)
        decision = getattr(commit, "decision", None)
        if task is None or decision is None:
            return self._no_continuation_proof(fence, "successor_commit_missing")
        return self._successor_execution_proof_from_task(
            fence,
            task=task,
            successor_execution_decision_id=str(decision.execution_decision_id),
            selection_request_id=selection_request_id,
        )

    @staticmethod
    def _release_ready_revision_conflict(exc: BaseException) -> bool:
        if isinstance(exc, TaskRevisionConflictError):
            return True
        from app.modules.research_execution_decision_service import (
            ExecutionPreflightBlocked,
        )

        return (
            isinstance(exc, ExecutionPreflightBlocked)
            and exc.code in {"task_revision_conflict", "instance_revision_not_current"}
        )

    def _release_ready_assignments_after_result(
        self,
        fence: DispatchContinuationFence,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> ContinuationResponsibilityProof:
        """Materialize same-Decision dependencies and prove their exact owner."""
        loader = getattr(self._repository, "load", None)
        if not callable(loader):
            return self._no_continuation_proof(fence, "dependency_release_task_reader_missing")
        load_scope = {
            "task_id": fence.task_id,
            "owner_id": owner_id,
            "workspace_id": workspace_id,
        }
        coordinator = self._coordinator()
        for attempt in range(_DEPENDENT_ASSIGNMENT_RELEASE_REVISION_ATTEMPTS):
            task = loader(**load_scope)
            decision = next(
                (
                    row
                    for row in getattr(task, "execution_decisions", ())
                    if row.execution_decision_id == fence.execution_decision_id
                ),
                None,
            )
            if decision is None:
                return self._no_continuation_proof(
                    fence, "dependency_release_not_applicable"
                )
            materialized_work_unit_ids = {
                row.work_unit_id
                for row in task.work_units
                if row.execution_decision_id == fence.execution_decision_id
            }
            if all(
                assignment.assignment_id
                in {
                    row.assignment_id
                    for row in task.work_units
                    if row.execution_decision_id == fence.execution_decision_id
                }
                for assignment in decision.assignments
            ):
                return self._no_continuation_proof(fence, "dependency_release_not_needed")
            try:
                released_task = coordinator.release_ready(
                    task_id=fence.task_id,
                    execution_decision_id=fence.execution_decision_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    expected_task_revision=task.revision,
                )
            except Exception as exc:
                if (
                    self._release_ready_revision_conflict(exc)
                    and attempt + 1 < _DEPENDENT_ASSIGNMENT_RELEASE_REVISION_ATTEMPTS
                ):
                    continue
                if self._release_ready_revision_conflict(exc):
                    raise DispatchContinuationRetryableError(
                        "dependent_assignment_release_revision_changed"
                    ) from exc
                raise
            released_work_unit_ids = {
                row.work_unit_id
                for row in getattr(released_task, "work_units", ())
                if row.execution_decision_id == fence.execution_decision_id
            } - materialized_work_unit_ids
            if not released_work_unit_ids:
                return self._no_continuation_proof(fence, "dependency_release_no_new_work")
            return self._successor_execution_proof_from_task(
                fence,
                task=released_task,
                successor_execution_decision_id=fence.execution_decision_id,
                selection_request_id=None,
                candidate_work_unit_ids=released_work_unit_ids,
            )
        raise DispatchContinuationRetryableError(
            "dependent_assignment_release_revision_changed"
        )

    def process_next_dispatch_continuation(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> bool:
        fence = self._repository.claim_dispatch_continuation(
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            retry_delay_seconds=self._retry_delay_seconds(1),
        )
        if fence is None:
            return False
        with DispatchContinuationLeaseKeeper(
            self._repository,
            fence,
            lease_seconds=lease_seconds,
        ) as lease:
            if lease.lost:
                return False
            try:
                proof = self.process_continuation(fence)
            except Exception as exc:
                logging.getLogger("openair").exception(
                    "dispatch continuation failed dispatch=%s task=%s",
                    fence.dispatch_id,
                    fence.task_id,
                )
                retryable = isinstance(exc, DispatchContinuationRetryableError)
                lease.stop()
                if (
                    not retryable
                    and fence.attempt_count
                    >= max(
                        1,
                        settings.research_dispatch_continuation_internal_error_max_attempts,
                    )
                ):
                    return self._settle_continuation_cas(
                        fence,
                        operation="recoverable_failure",
                        settle=lambda: self._repository.fail_dispatch_continuation(
                            fence,
                            error_code="dispatch_continuation_internal_error_exhausted",
                            retry_delay_seconds=max(
                                1, settings.research_dispatch_retry_max_seconds
                            ),
                        ),
                    )
                return self._settle_continuation_cas(
                    fence,
                    operation="defer",
                    settle=lambda: self._repository.defer_dispatch_continuation(
                        fence,
                        error_code=(
                            str(exc)
                            if retryable
                            else "dispatch_continuation_internal_error"
                        ),
                        retry_delay_seconds=self._retry_delay_seconds(fence.attempt_count),
                    ),
                )
            lease.stop()
            return self._settle_processed_continuation(fence, proof=proof)

    @staticmethod
    def _settle_continuation_cas(
        fence: DispatchContinuationFence,
        *,
        operation: str,
        settle: Callable[[], bool],
    ) -> bool:
        settled = bool(settle())
        if not settled:
            logging.getLogger("openair").warning(
                "dispatch continuation %s rejected by fence dispatch=%s task=%s "
                "leaseOwner=%s attempt=%s",
                operation,
                fence.dispatch_id,
                fence.task_id,
                fence.lease_owner,
                fence.attempt_count,
            )
        return settled

    def _execution_stop_active(self, fence: DispatchContinuationFence) -> bool:
        stop_active = getattr(self._repository, "execution_stop_active", None)
        return bool(
            callable(stop_active)
            and stop_active(
                task_id=fence.task_id,
                owner_id=fence.owner_id,
                workspace_id=fence.workspace_id,
            )
        )

    def _no_proof_continuation_terminal_error(
        self,
        fence: DispatchContinuationFence,
        proof: ContinuationResponsibilityProof,
    ) -> str | None:
        if proof.kind is not ContinuationResponsibilityKind.NO_PROOF:
            return None
        reason = (proof.reason_code or "").strip()
        if reason != _DISPATCH_CONTINUATION_NO_RESPONSIBILITY_PROOF:
            return None
        max_attempts = max(
            1,
            settings.research_dispatch_continuation_no_responsibility_proof_max_attempts,
        )
        if fence.attempt_count >= max_attempts:
            return "dispatch_continuation_no_responsibility_proof_exhausted"
        task = self._load_continuation_task(fence)
        if task is None:
            return None
        status = str(self._value(task, "status") or "")
        if status in _TERMINAL_TASK_STATUSES:
            return "dispatch_continuation_no_responsibility_proof_task_terminal"
        decision = next(
            (
                row
                for row in getattr(task, "execution_decisions", ())
                if self._value(row, "execution_decision_id") == fence.execution_decision_id
            ),
            None,
        )
        if decision is not None and self._value(decision, "status") != "dispatched":
            return "dispatch_continuation_no_responsibility_proof_decision_closed"
        return None

    def _settle_processed_continuation(
        self,
        fence: DispatchContinuationFence,
        *,
        proof: ContinuationResponsibilityProof,
    ) -> bool:
        if proof.kind is not ContinuationResponsibilityKind.NO_PROOF:
            completed = self._repository.complete_dispatch_continuation(
                fence, proof=proof
            )
            if completed:
                return True
            logging.getLogger("openair").warning(
                "dispatch continuation proof rejected dispatch=%s task=%s kind=%s",
                fence.dispatch_id,
                fence.task_id,
                proof.kind.value,
            )
            # A proof can become stale between service formation and the
            # repository's locked re-check.  If this fence still owns the row,
            # convert that rejection to the same bounded NO_PROOF retry rather
            # than waiting for lease expiry.  A stale fence simply fails the CAS.
            return self._settle_continuation_cas(
                fence,
                operation="defer:proof_rejected",
                settle=lambda: self._repository.defer_dispatch_continuation(
                    fence,
                    error_code="dispatch_continuation_proof_rejected",
                    retry_delay_seconds=self._retry_delay_seconds(fence.attempt_count),
                ),
            )
        terminal_error = self._no_proof_continuation_terminal_error(fence, proof)
        if terminal_error is not None:
            return self._settle_continuation_cas(
                fence,
                operation="terminalize:no_proof",
                settle=lambda: self._repository.terminalize_dispatch_continuation(
                    fence,
                    error_code=terminal_error,
                ),
            )
        return self._settle_continuation_cas(
            fence,
            operation="defer:no_proof",
            settle=lambda: self._repository.defer_dispatch_continuation(
                fence,
                error_code=proof.reason_code or "dispatch_continuation_no_proof",
                retry_delay_seconds=self._retry_delay_seconds(fence.attempt_count),
            ),
        )

    def resume_pending_method_continuation(self, *, min_pending_age_seconds: int = 0) -> bool:
        reader = getattr(self._repository, "list_pending_method_continuations", None)
        if not callable(reader):
            return False
        for row in reader(min_pending_age_seconds=min_pending_age_seconds):
            try:
                task = self._coordinator().release_ready(
                    task_id=row["task_id"], execution_decision_id=row["execution_decision_id"],
                    owner_id=row["owner_id"], workspace_id=row["workspace_id"],
                    expected_task_revision=row["revision"],
                )
                if task.revision != row["revision"]:
                    return True
            except TaskRevisionConflictError:
                continue
            except Exception as exc:
                from app.modules.research_execution_decision_service import (
                    ExecutionPreflightBlocked,
                )
                if isinstance(exc, ExecutionPreflightBlocked) and exc.code == "task_revision_conflict":
                    continue
                logging.getLogger("openair").exception(
                    "method continuation remains pending task=%s decision=%s",
                    row["task_id"], row["execution_decision_id"],
                )
        return False

    @staticmethod
    def _value(row: Any, field: str) -> Any:
        """Read a Pydantic enum field without weakening the durable-fact check."""
        value = getattr(row, field, None)
        return getattr(value, "value", value)

    def _recover_confirmed_semantic_material_gap(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> bool:
        """Create a user-confirmable recovery Design draft after the gap is durable.

        ``record_dispatch_completed_result`` owns the semantic-gap transaction.
        This method runs afterwards and re-derives the confirmed Design from the
        committed Task, so recovery cannot build a draft from stale state.
        """
        loader = getattr(self._repository, "load", None)
        if not callable(loader):
            return False
        task = loader(
            task_id=fence.task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        decision = next(
            (
                row for row in task.execution_decisions
                if row.execution_decision_id == fence.execution_decision_id
            ),
            None,
        )
        active_ref = getattr(task, "active_research_design_ref", None)
        if active_ref is None:
            return False
        active = next(
            (
                row for row in getattr(task, "research_design_versions", ())
                if active_ref is not None
                and row.research_design_id == active_ref.research_design_id
                and row.version == active_ref.version
            ),
            None,
        )
        if not (
            self._value(task, "execution_mode") == "automatic_research"
            and decision is not None
            and self._value(decision, "purpose") == "completion"
            and self._value(decision, "status") == "closed"
            and self._value(decision, "outcome") == "blocked"
            and active is not None
            and self._value(active, "status") == "confirmed"
            and decision.research_design_version == active.version
        ):
            return False
        runtime = next(
            (
                row for row in getattr(task, "substage_runtime_states", ())
                if row.substage_instance_id == decision.substage_instance_id
                and row.instance_revision == decision.substage_instance_revision
            ),
            None,
        )
        work = next(
            (
                row for row in getattr(task, "work_units", ())
                if row.work_unit_id == fence.work_unit_id
                and row.execution_decision_id == decision.execution_decision_id
                and self._value(row, "status") == "blocked"
                and self._value(row, "terminal_reason_code")
                == "semantic_material_insufficient"
            ),
            None,
        )
        artifact = next(
            (
                row for row in getattr(task, "artifacts", ())
                if work is not None
                and row.work_unit_id == work.work_unit_id
                and self._value(row, "artifact_state") == "insufficient_materials"
                and isinstance(getattr(row, "validator_result", None), dict)
                and row.validator_result.get("semanticReadiness", {}).get("explicit") is True
                and row.validator_result.get("semanticReadiness", {}).get("downstreamReady") is False
            ),
            None,
        )
        if not (
            runtime is not None
            and self._value(runtime, "status") == "skipped"
            and self._value(runtime, "status_reason_code") == "semantic_material_insufficient"
            and work is not None
            and artifact is not None
        ):
            return False
        from app.modules.production_definition_source import ProductionDefinitionSource
        from app.modules.research_design_service import ResearchDesignService

        source = ProductionDefinitionSource(
            snapshot_ref=task.registry_snapshot_ref.model_dump(mode="json", by_alias=True)
        )
        ResearchDesignService(
            repository=self._repository,
            definition_source=source,
        ).recover_confirmed_semantic_material_gap(
            task_id=fence.task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            blocked_instance_id=decision.substage_instance_id,
            gap_reason_code="semantic_material_insufficient",
            expected_task_revision=task.revision,
        )
        return True

    def _controller_commands(self):
        if self._controller_command_repository is None:
            from app.modules.controller_command_repository import ControllerCommandRepository

            self._controller_command_repository = ControllerCommandRepository()
        return self._controller_command_repository

    def _dispatch_execution_purpose(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> str:
        """Read purpose from the durable Decision lineage; never infer it from outputs."""
        task = self._repository.load(
            task_id=fence.task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        decision = next(
            (
                row
                for row in task.execution_decisions
                if row.execution_decision_id == fence.execution_decision_id
            ),
            None,
        )
        work_unit = next(
            (row for row in task.work_units if row.work_unit_id == fence.work_unit_id),
            None,
        )
        assignment = None if decision is None or work_unit is None else next(
            (
                row
                for row in decision.assignments
                if row.assignment_id == work_unit.assignment_id
            ),
            None,
        )
        if (
            decision is None
            or work_unit is None
            or assignment is None
            or work_unit.execution_decision_id != decision.execution_decision_id
        ):
            raise RuntimeError("dispatch_decision_lineage_invalid")
        return decision.purpose.value

    @staticmethod
    def _controller_recovery_target(task: Any, *, original_decision_id: str) -> tuple[Any, Any]:
        decision = next(
            (
                row
                for row in task.execution_decisions
                if row.execution_decision_id == original_decision_id
            ),
            None,
        )
        if decision is None or decision.status.value != "closed":
            raise RuntimeError("controller_command_terminal_decision_missing")
        runtime = next(
            (
                row
                for row in task.substage_runtime_states
                if row.substage_instance_id == decision.substage_instance_id
                and row.instance_revision == decision.substage_instance_revision
            ),
            None,
        )
        if (
            runtime is None
            or runtime.active_execution_decision_id is not None
            or runtime.status.value
            not in {"needs_input", "blocked_recoverable", "insufficient_evidence"}
        ):
            raise RuntimeError("controller_command_substage_not_recoverable")
        purpose = decision.purpose.value
        outcome = None if decision.outcome is None else decision.outcome.value
        if purpose == "progress":
            allowed = {"progressed", "needs_input", "blocked"}
        elif purpose == "completion":
            allowed = {"rejected", "needs_input", "blocked"}
        else:
            allowed = set()
        if outcome not in allowed:
            raise RuntimeError("controller_command_decision_not_recoverable")
        primary_assignment_ids = {
            row.assignment_id for row in decision.assignments if row.role.value == "primary"
        }
        work_units = sorted(
            (
                row
                for row in task.work_units
                if row.execution_decision_id == decision.execution_decision_id
                and row.assignment_id in primary_assignment_ids
            ),
            key=lambda row: row.work_unit_id,
        )
        if not work_units:
            raise RuntimeError("controller_command_primary_work_unit_missing")
        return decision, work_units[0]

    def progress_controller_command(
        self,
        task_id: str,
        command_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
    ):
        """Materialize exactly one canonical revision for one durable supplement command."""
        if type(expected_task_revision) is not int or expected_task_revision < 0:
            raise ValueError("expected_task_revision must be a non-negative integer")
        from app.modules.controller_command_repository import (
            canonical_progression_recovery_identity,
            canonical_progression_selection_request_id,
        )

        commands = self._controller_commands()
        context = commands.get_progression_context(
            task_id=task_id,
            command_id=command_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        if context is None:
            raise RuntimeError("controller_command_not_found")
        if context["state"] == "applied":
            return self._repository.load(
                task_id=task_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        if (
            context["state"] != "received"
            or context["kind"] != "supplement_input"
            or context["lifecycleKind"] != "canonical_v1_2"
        ):
            raise RuntimeError("controller_command_not_progressable")
        submitted_revision = int(context["submittedTaskRevision"])
        if submitted_revision + 1 != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")

        lease_expires_at = context.get("progressionLeaseExpiresAt")
        lease_active = bool(context.get("progressionLeaseOwner")) and (
            not isinstance(lease_expires_at, datetime)
            or lease_expires_at > datetime.now(timezone.utc)
        )
        if not lease_active:
            claim = commands.claim_received_for_progression(
                task_id=task_id,
                command_id=command_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                lease_owner=f"research-dispatch-service:{command_id}",
            )
            if claim is None:
                refreshed = commands.get_progression_context(
                    task_id=task_id,
                    command_id=command_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                )
                if refreshed is not None and refreshed["state"] == "applied":
                    return self._repository.load(
                        task_id=task_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                    )
                raise RuntimeError("controller_command_progression_claim_conflict")

        task = self._repository.load(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        if task.revision != expected_task_revision:
            raise TaskRevisionConflictError("task_revision_conflict")
        if task.status.value in {"completed", "failed", "cancelled"}:
            raise RuntimeError("controller_command_task_terminal")
        boundary = context.get("targetBoundary") or {}
        original_decision_id = str(
            boundary.get("submittedAfterExecutionDecisionId") or ""
        ).strip()
        if not original_decision_id:
            raise RuntimeError("controller_command_terminal_decision_missing")
        known_work_unit_ids = {
            str(value).strip()
            for value in boundary.get("knownWorkUnitIds") or []
            if str(value).strip()
        }
        if {row.work_unit_id for row in task.work_units} != known_work_unit_ids:
            raise TaskRevisionConflictError("controller_command_boundary_changed")
        original, original_work_unit = self._controller_recovery_target(
            task,
            original_decision_id=original_decision_id,
        )
        progressor = getattr(self._coordinator(), "materialize_controller_progression", None)
        if not callable(progressor):
            raise RuntimeError("controller_command_progression_service_unavailable")
        result = progressor(
            task_id=task_id,
            original_execution_decision_id=original.execution_decision_id,
            original_work_unit_id=original_work_unit.work_unit_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            selection_request_id=canonical_progression_selection_request_id(command_id),
            expected_task_revision=expected_task_revision,
            recovery_identity=canonical_progression_recovery_identity(command_id),
            directive_kind="research_revision",
        )
        # Materialization is not application. The command remains received until
        # a subsequent Dispatch is durably authorized with the supplement in its
        # execution payload.
        return result

    def claim(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
        batch_size: int = 1,
        dispatch_id: str | None = None,
        task_id: str | None = None,
        owner_id: str | None = None,
        workspace_id: str | None = None,
        min_pending_age_seconds: int = 0,
        max_inflight_per_task: int | None = None,
    ) -> list[DispatchFence]:
        claim_kwargs = {
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
            "batch_size": batch_size,
            "dispatch_id": dispatch_id,
            "task_id": task_id,
            "owner_id": owner_id,
            "workspace_id": workspace_id,
        }
        if min_pending_age_seconds:
            claim_kwargs['min_pending_age_seconds'] = min_pending_age_seconds
        if max_inflight_per_task is not None:
            claim_kwargs['max_inflight_per_task'] = max_inflight_per_task
        return self._repository.claim_dispatches(**claim_kwargs)

    def renew(self, fence: DispatchFence, *, lease_seconds: int) -> bool:
        return self._repository.renew_dispatch(fence, lease_seconds=lease_seconds)

    def claim_authorized_recovery(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
        batch_size: int = 1,
        dispatch_id: str | None = None,
        task_id: str | None = None,
        owner_id: str | None = None,
        workspace_id: str | None = None,
        max_inflight_per_task: int | None = None,
    ) -> list[AuthorizedDispatch]:
        return self._repository.claim_authorized_recovery(
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            batch_size=batch_size,
            dispatch_id=dispatch_id,
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            max_inflight_per_task=max_inflight_per_task,
        )

    def renew_authorized(self, fence: AuthorizedDispatch, *, lease_seconds: int) -> bool:
        return self._repository.renew_authorized_dispatch(fence, lease_seconds=lease_seconds)

    def authorize(
        self,
        fence: DispatchFence,
        *,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        capabilities: ProviderSubmissionSafety,
        lease_seconds: int = 30,
    ) -> AuthorizedDispatch | None:
        require_safe_submission(capabilities)
        return self._repository.authorize_dispatch_submission(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
            submission_mode=capabilities.submissionMode,
            lease_seconds=lease_seconds,
        )

    def mark_external_action_started(
        self,
        fence: AuthorizedDispatch,
        *,
        provider_id: str,
        adapter: str,
        lease_seconds: int = 30,
    ) -> AuthorizedDispatch | None:
        return self._repository.mark_external_action_started(
            fence,
            provider_id=provider_id,
            adapter=adapter,
            lease_seconds=lease_seconds,
        )

    def prepare_external_execution(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        execution_kind: str,
        provider_id: str,
        adapter: str,
        request_body: bytes,
    ):
        from app.modules.external_execution_checkpoint import (
            ExternalExecutionCheckpointRepository,
        )

        return ExternalExecutionCheckpointRepository().prepare(
            capability_call_id=fence.external_attempt.capability_call_id,
            dispatch_id=fence.dispatch_id,
            execution_kind=execution_kind,
            provider_id=provider_id,
            adapter=adapter,
            request_body=request_body,
        )

    def get_external_execution_checkpoint(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
    ):
        from app.modules.external_execution_checkpoint import (
            ExternalExecutionCheckpointRepository,
        )

        return ExternalExecutionCheckpointRepository().get(
            capability_call_id=fence.external_attempt.capability_call_id
        )

    def stage_external_execution_result(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        payload: bytes,
    ):
        from app.modules.external_execution_checkpoint import (
            ExternalExecutionCheckpointRepository,
        )

        return ExternalExecutionCheckpointRepository().stage_response(
            capability_call_id=fence.external_attempt.capability_call_id,
            response_kind="dispatch_completed",
            payload=payload,
        )

    @staticmethod
    def _retry_delay_seconds(attempt_count: int) -> int:
        base = max(1, settings.research_dispatch_retry_base_seconds)
        cap = max(base, settings.research_dispatch_retry_max_seconds)
        exponent = max(0, min(int(attempt_count) - 1, 10))
        return min(cap, base * (2**exponent))

    def defer_unsubmitted(
        self,
        fence: DispatchFence,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str,
    ) -> str:
        return self._repository.record_dispatch_retryable_failure(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=error_code,
            max_attempts=max(1, settings.research_dispatch_max_attempts),
            retry_delay_seconds=self._retry_delay_seconds(fence.attempt_count),
        )

    def defer_authorized(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str,
    ) -> str:
        return self._repository.record_dispatch_retryable_failure(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=error_code,
            max_attempts=max(1, settings.research_dispatch_max_attempts),
            retry_delay_seconds=self._retry_delay_seconds(fence.attempt_count),
        )

    def fail_authorized_unsubmitted(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str,
    ) -> bool:
        return self._repository.record_dispatch_retryable_failure(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=error_code,
            max_attempts=1,
            retry_delay_seconds=1,
        ) == "failed"

    @staticmethod
    def _result_storage_ref(fence: AuthorizedDispatch) -> str:
        return f"durable/research-dispatch/{fence.dispatch_id}/result.json"

    @staticmethod
    def _artifact_storage_ref(fence: AuthorizedDispatch) -> str:
        return f"durable/research-dispatch/{fence.dispatch_id}/artifact.json"

    def record_completed_result(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        result: DispatchSubmissionCompleted,
    ) -> bool:
        return self._record_completed_result(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            result=result,
            allow_closed_decision_for_result_recovery=False,
        )

    def _record_completed_result(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        result: DispatchSubmissionCompleted,
        allow_closed_decision_for_result_recovery: bool,
    ) -> bool:
        reload = getattr(
            self._repository, "reload_authorized_dispatch_for_result_persistence", None
        )
        if callable(reload):
            refreshed = reload(fence)
            if refreshed is not None:
                fence = refreshed
        request = submission_request_from_authorized(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        executor_type = str(dict(fence.work_unit_payload).get("executorRef", {}).get("executorType") or "agent")
        if executor_type == "agent":
            frozen = validate_completed_dispatch_result(
                request,
                result,
                repository=self._repository,
            )
        elif executor_type == "skill":
            from app.execution.research_dispatch_skill_provider import (
                validate_completed_skill_dispatch_result,
            )

            frozen = validate_completed_skill_dispatch_result(
                request,
                result,
                repository=self._repository,
                allow_closed_decision_for_result_recovery=(
                    allow_closed_decision_for_result_recovery
                ),
            )
        else:
            raise RuntimeError("dispatch_executor_type_unsupported")
        envelope = {
            "schemaVersion": 2,
            "dispatchId": fence.dispatch_id,
            "title": result.title,
            "mimeType": result.mime_type,
            "content": result.content,
            "providerId": result.provider_id,
            "adapter": result.adapter,
            "model": result.model,
            "resultProtocol": dict(result.result_protocol),
            "warnings": list(result.warnings),
            "externalActionEvidence": [dict(item) for item in result.external_action_evidence],
            "artifactContract": frozen.artifactContract,
            "artifactSchemaDigest": frozen.artifactSchemaDigest,
        }
        blob = json.dumps(
            envelope, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        reserved_ref = str(fence.result_storage_ref or "").strip() or None
        reserved_type = str(fence.result_storage_type or "").strip() or None
        reserved_scope = fence.result_storage_scope
        if reserved_ref and reserved_type:
            storage_ref = reserved_ref
            storage_type = reserved_type
            storage_scope = (
                reserved_scope
                if storage_type == "external"
                else fence.task_id
            )
        else:
            storage_ref = self._result_storage_ref(fence)
            storage_type = "external" if settings.artifact_storage == "minio" else "local"
            if storage_type == "external":
                from app.modules.minio_client import current_minio_storage_scope

                storage_scope = current_minio_storage_scope()
            else:
                storage_scope = fence.task_id
        reserve = getattr(self._repository, "reserve_dispatch_result_storage", None)
        if callable(reserve) and not reserve(
            fence,
            storage_type=storage_type,
            storage_scope=storage_scope,
            storage_ref=storage_ref,
        ):
            raise RuntimeError("dispatch_result_storage_reservation_conflict")
        if storage_type == "external":
            try:
                stored = artifact_write_service.store_durable_bytes(
                    fence.task_id, storage_ref, blob, "application/json", storage_scope=storage_scope
                )
            except TypeError:
                stored = artifact_write_service.store_durable_bytes(
                    fence.task_id, storage_ref, blob, "application/json"
                )
        else:
            stored = artifact_write_service.store_durable_bytes(
                fence.task_id, storage_ref, blob, "application/json"
            )
        stored_scope = getattr(stored, "storage_scope", storage_scope)
        if stored.storage_type == "external":
            try:
                stored_read = artifact_write_service.read_durable_bytes(
                    fence.task_id, stored.storage_type, stored.storage_ref, stored_scope
                )
            except TypeError:
                stored_read = artifact_write_service.read_durable_bytes(
                    fence.task_id, stored.storage_type, stored.storage_ref
                )
        else:
            stored_read = artifact_write_service.read_durable_bytes(
                fence.task_id, stored.storage_type, stored.storage_ref
            )
        if stored_read != blob:
            raise RuntimeError("dispatch_result_blob_checksum_mismatch")
        accepted_artifact_content, source_identity_acceptance = (
            validate_result_external_sources(
                result.content,
                frozen.orchestrationPlan.get("externalSourceRecords"),
                result_protocol=result.result_protocol,
            )
        )
        if source_identity_acceptance["status"] == "validated":
            source_identity_acceptance = {
                **source_identity_acceptance,
                "skillId": frozen.skill_id,
                "definitionVersion": frozen.definition_version,
            }
        artifact_storage_type = None
        artifact_storage_scope = None
        artifact_storage_ref = None
        assignment_role = frozen.orchestrationPlan.get("assignmentRole")
        produced_artifact_type = str(
            frozen.orchestrationPlan.get("artifactKind") or ""
        ).strip() or None
        execution_purpose = None
        if assignment_role != "reviewer":
            execution_purpose = str(frozen.orchestrationPlan.get("executionPurpose") or "").strip()
            if not execution_purpose:
                try:
                    execution_purpose = self._dispatch_execution_purpose(
                        fence,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                    )
                except AttributeError:
                    # Keep backwards compatibility for isolated tests that stub only
                    # persistence methods used by record_completed_result.
                    execution_purpose = "completion"
        frozen_expected_artifact_types = [
            str(value).strip()
            for value in dict(fence.work_unit_payload).get("expectedArtifactTypes") or []
            if str(value).strip()
        ]
        if assignment_role != "reviewer" and (
            execution_purpose == "completion"
            or produced_artifact_type is not None
            or frozen_expected_artifact_types
        ):
            artifact_blob = accepted_artifact_content.encode("utf-8")
            if stored.storage_type == "external":
                try:
                    artifact_stored = artifact_write_service.store_durable_bytes(
                        fence.task_id,
                        self._artifact_storage_ref(fence),
                        artifact_blob,
                        result.mime_type,
                        storage_scope=stored_scope,
                    )
                except TypeError:
                    artifact_stored = artifact_write_service.store_durable_bytes(
                        fence.task_id, self._artifact_storage_ref(fence), artifact_blob, result.mime_type
                    )
            else:
                artifact_stored = artifact_write_service.store_durable_bytes(
                    fence.task_id, self._artifact_storage_ref(fence), artifact_blob, result.mime_type
                )
            artifact_scope = getattr(artifact_stored, "storage_scope", stored_scope)
            if artifact_stored.storage_type == "external":
                try:
                    artifact_read = artifact_write_service.read_durable_bytes(
                        fence.task_id, artifact_stored.storage_type, artifact_stored.storage_ref, artifact_scope
                    )
                except TypeError:
                    artifact_read = artifact_write_service.read_durable_bytes(
                        fence.task_id, artifact_stored.storage_type, artifact_stored.storage_ref
                    )
            else:
                artifact_read = artifact_write_service.read_durable_bytes(
                    fence.task_id, artifact_stored.storage_type, artifact_stored.storage_ref
                )
            if artifact_read != artifact_blob:
                raise RuntimeError("dispatch_artifact_blob_checksum_mismatch")
            artifact_storage_type = artifact_stored.storage_type
            artifact_storage_scope = artifact_scope
            artifact_storage_ref = artifact_stored.storage_ref
        scientific_state_projection = frozen.orchestrationPlan.get("scientificStateProjection")
        from app.modules.scientific_state_fact_projection import project_validated_structured_result

        scientific_state_fact_projections = project_validated_structured_result(
            accepted_artifact_content,
            scientific_state_projection
            if isinstance(scientific_state_projection, Mapping) else None,
            result_protocol=result.result_protocol,
        )

        persisted = self._repository.record_dispatch_completed_result(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            result=result,
            artifact_contract=frozen.artifactContract,
            artifact_schema_digest=frozen.artifactSchemaDigest,
            storage_type=stored.storage_type,
            storage_scope=stored_scope,
            storage_ref=stored.storage_ref,
            content_sha256=hashlib.sha256(result.content.encode("utf-8")).hexdigest(),
            artifact_storage_type=artifact_storage_type,
            artifact_storage_scope=artifact_storage_scope,
            artifact_storage_ref=artifact_storage_ref,
            produced_artifact_type=produced_artifact_type,
            accepted_artifact_content=accepted_artifact_content,
            source_identity_acceptance=source_identity_acceptance,
            scientific_state_fact_projections=scientific_state_fact_projections,
            scientific_state_projection_declaration=(
                dict(scientific_state_projection)
                if isinstance(scientific_state_projection, Mapping)
                else None
            ),
        )
        if not persisted:
            return False
        # Dispatch settlement and continuation enqueue committed with the Result.
        return True

    def process_continuation(
        self,
        fence: DispatchContinuationFence,
    ) -> ContinuationResponsibilityProof:
        """Advance a completed Result; ``NO_PROOF`` retains bounded-backoff ownership."""
        owner_id = fence.owner_id
        workspace_id = fence.workspace_id
        stop_active, waiting_for_design_confirmation = prepare_continuation_preconditions(
            self,
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            persistence_error=ResearchOrchestrationPersistenceError,
            revision_conflict_error=TaskRevisionConflictError,
            retryable_error=DispatchContinuationRetryableError,
        )
        if stop_active:
            # The Result/Artifact is already durable.  Stop prevents further
            # dependency release or new selection, but completion still requires
            # the durable stop receipt to be named and re-checked by repository.
            return self._user_stopped_proof(fence)
        if waiting_for_design_confirmation:
            return self._user_responsibility_proof(
                fence,
                reason_code="research_design_confirmation_required",
            )

        same_decision_proof = self._release_ready_assignments_after_result(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )
        system_recovery_pending = self._system_reconcile_external_result(fence)
        from app.modules.continuation_lineage import prepare_lineage_boundary

        boundary = prepare_lineage_boundary(self, fence)
        if boundary.proof is not None:
            return boundary.proof
        directive = boundary.directive
        if directive.action == "terminal":
            task = self._load_continuation_task(fence)
            if task is None:
                return self._no_continuation_proof(fence, "terminal_task_missing")
            return self._task_terminal_proof(fence, task)
        if directive.action == "stopped":
            return self._user_stopped_proof(fence)
        if directive.action == "pause":
            if not directive.reason_code:
                return self._no_continuation_proof(fence, "user_gate_reason_missing")
            return self._user_responsibility_proof(
                fence,
                reason_code=directive.reason_code,
                target_instance_id=directive.next_instance_id,
            )
        if directive.action == "waiting_user":
            # Compatibility only.  The repository no longer manufactures this
            # action from Task.status; it must carry a real user-owned reason.
            if not directive.reason_code:
                return self._no_continuation_proof(fence, "user_gate_reason_missing")
            return self._user_responsibility_proof(
                fence,
                reason_code=directive.reason_code,
                target_instance_id=directive.next_instance_id,
                selection_request_id=directive.selection_request_id,
            )
        if directive.action == "start":
            if directive.selection_request_id is None:
                raise DispatchContinuationRetryableError(
                    "execution_continuation_identity_missing"
                )
            try:
                coordinator = self._coordinator()
                if directive.next_instance_id is None:
                    commit = coordinator.start_next(
                        task_id=fence.task_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        selection_request_id=directive.selection_request_id,
                        expected_task_revision=directive.expected_task_revision,
                    )
                else:
                    # Reviewer rejection/recovery is an already-authorized retry
                    # of the same research task, not a task-scoped witness.
                    commit = coordinator.start(
                        task_id=fence.task_id,
                        instance_id=directive.next_instance_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        selection_request_id=directive.selection_request_id,
                        expected_task_revision=directive.expected_task_revision,
                    )
            except TaskRevisionConflictError as exc:
                raise DispatchContinuationRetryableError(
                    "execution_continuation_revision_changed"
                ) from exc
            except Exception as exc:
                from app.modules.research_execution_decision_service import (
                    ExecutionPreflightBlocked,
                )

                if not isinstance(exc, ExecutionPreflightBlocked):
                    raise
                if exc.code in {"task_revision_conflict", "instance_revision_not_current"}:
                    raise DispatchContinuationRetryableError(
                        "execution_continuation_revision_changed"
                    ) from exc
                if needs_user_input(exc.code):
                    return self._blocked_selection_user_proof(
                        fence,
                        selection_request_id=directive.selection_request_id,
                        reason_code=exc.code,
                    )
                if directive.next_instance_id is None:
                    # No user-owned input was proven and no successor exists yet.
                    # Retain this continuation rather than turning the blocker into
                    # an implicit user wait.
                    raise DispatchContinuationRetryableError(
                        "execution_continuation_preflight_blocked"
                    ) from exc
                already_handled = bool(exc.facts.get("taskRevision")) or exc.code in {
                    "active_design_missing",
                    "active_design_not_confirmed",
                    "instance_revision_not_current",
                    "instance_not_ready",
                    "task_not_executable",
                    "active_dispatched_decision_exists",
                }
                if not already_handled:
                    try:
                        self._repository.record_execution_continuation_blocked(
                            task_id=fence.task_id,
                            owner_id=owner_id,
                            workspace_id=workspace_id,
                            expected_task_revision=directive.expected_task_revision,
                            next_instance_id=directive.next_instance_id,
                            reason_code=exc.code,
                        )
                    except TaskRevisionConflictError as revision_exc:
                        raise DispatchContinuationRetryableError(
                            "execution_continuation_revision_changed"
                        ) from revision_exc
                return self._no_continuation_proof(fence, "execution_continuation_system_blocked")
            if boundary.first_successor_id is not None:
                return self._successor_execution_proof_from_task(
                    fence,
                    task=commit.task,
                    successor_execution_decision_id=boundary.first_successor_id,
                    selection_request_id=boundary.first_selection_request_id,
                )
            return self._successor_execution_proof_from_commit(
                fence,
                commit=commit,
                selection_request_id=directive.selection_request_id,
            )
        transfer = self._continuation_transfer_proof(
            fence,
            same_decision_proof=same_decision_proof,
            system_recovery_pending=system_recovery_pending,
        )
        if transfer is not None:
            return transfer
        return self._no_continuation_proof(
            fence, "dispatch_continuation_no_responsibility_proof"
        )

    def _continuation_transfer_proof(
        self,
        fence: DispatchContinuationFence,
        *,
        same_decision_proof: ContinuationResponsibilityProof,
        system_recovery_pending: bool,
    ) -> ContinuationResponsibilityProof | None:
        """Name a repository-CAS-completable owner for the next executable WorkUnit."""
        if (
            same_decision_proof.kind
            is ContinuationResponsibilityKind.SUCCESSOR_EXECUTION
        ):
            return same_decision_proof
        if not system_recovery_pending:
            return None
        return self._system_recovery_transfer_proof(fence)

    def _system_recovery_transfer_proof(
        self,
        fence: DispatchContinuationFence,
    ) -> ContinuationResponsibilityProof | None:
        owner_reader = getattr(
            self._repository, "load_continuation_owner_for_work_unit", None
        )
        if not callable(owner_reader):
            return None
        owner = owner_reader(
            task_id=fence.task_id,
            owner_id=fence.owner_id,
            workspace_id=fence.workspace_id,
            execution_decision_id=fence.execution_decision_id,
            work_unit_id=fence.work_unit_id,
        )
        if owner is None:
            return None
        return ContinuationResponsibilityProof(
            kind=ContinuationResponsibilityKind.SYSTEM_RECOVERY,
            source_execution_decision_id=fence.execution_decision_id,
            reason_code="external_result_recovery_due",
            target_type="work_unit",
            target_id=fence.work_unit_id,
            owner_kind=owner.kind,
            owner_id=owner.owner_id,
        )

    def recover_completed_result(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> bool:
        storage_type = fence.result_storage_type
        storage_scope = fence.result_storage_scope
        storage_ref = fence.result_storage_ref
        if not storage_type or not storage_ref:
            # Legacy/pre-reservation local dispatches can still use the deterministic local path.
            if settings.artifact_storage != "local":
                return False
            storage_type = "local"
            storage_scope = fence.task_id
            storage_ref = self._result_storage_ref(fence)
        if storage_type == "external" and not storage_scope:
            return False
        blob = (
            artifact_write_service.read_durable_bytes(
                fence.task_id, storage_type, storage_ref, storage_scope
            )
            if storage_type == "external"
            else artifact_write_service.read_durable_bytes(
                fence.task_id, storage_type, storage_ref
            )
        )
        if blob is None:
            return False
        try:
            envelope = json.loads(blob.decode("utf-8"))
            if not isinstance(envelope, dict) or envelope.get("dispatchId") != fence.dispatch_id:
                return False
            result = DispatchSubmissionCompleted(
                title=envelope["title"],
                mime_type=envelope["mimeType"],
                content=envelope["content"],
                provider_id=envelope["providerId"],
                adapter=envelope["adapter"],
                external_action_evidence=tuple(envelope.get("externalActionEvidence") or ()),
                model=str(envelope.get("model") or "") or None,
                result_protocol=(
                    envelope.get("resultProtocol")
                    if isinstance(envelope.get("resultProtocol"), dict)
                    else {}
                ),
                warnings=tuple(envelope.get("warnings") or ()),
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        finalize_stopped = getattr(
            self._repository, "finalize_stopped_persisted_dispatch_result", None
        )
        if callable(finalize_stopped) and finalize_stopped(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            content_sha256=hashlib.sha256(result.content.encode("utf-8")).hexdigest(),
        ):
            return True
        return self._record_completed_result(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            result=result,
            # A validated durable envelope may outlive the open Decision that
            # created it. This path only re-materializes that exact result; it
            # never submits external work.
            allow_closed_decision_for_result_recovery=True,
        )

    def record_outcome(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
        outcome: str,
        remote_run_id: str | None = None,
        error_code: str | None = None,
        failure_detail: Mapping[str, str] | None = None,
        provider_id: str | None = None,
        adapter: str | None = None,
    ) -> bool:
        if not isinstance(fence, AuthorizedDispatch):
            raise TypeError("recording an outcome requires AuthorizedDispatch")
        recorded = self._repository.record_dispatch_outcome(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            outcome=outcome,
            remote_run_id=remote_run_id,
            error_code=error_code,
            failure_detail=failure_detail,
            provider_id=provider_id,
            adapter=adapter,
        )
        if recorded and outcome in {"known_rejected", "known_failed"}:
            self._maybe_auto_system_explicit_retry(
                fence,
                owner_id=owner_id,
                workspace_id=workspace_id,
                error_code=error_code,
            )
        return recorded

    def fail_unsubmitted(
        self,
        fence: DispatchFence,
        *,
        owner_id: str,
        workspace_id: str,
        error_code: str,
    ) -> bool:
        return self._repository.fail_unsubmitted_dispatch(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
            error_code=error_code,
        )

    def revalidate_recovery_submission(
        self,
        fence: AuthorizedDispatch,
        *,
        owner_id: str,
        workspace_id: str,
    ) -> str:
        return self._repository.revalidate_recovery_submission(
            fence,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )

    def suppress(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
        reason_code: str = "task_stopped",
    ) -> int:
        if type(expected_task_revision) is not int or expected_task_revision < 0:
            raise ValueError("expected_task_revision must be a non-negative integer")
        return self._repository.suppress_dispatches(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
            reason_code=reason_code,
        )

    def suppress_with_receipt(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int | None,
        reason_code: str = "task_stopped",
    ) -> dict[str, Any]:
        if expected_task_revision is not None and (
            type(expected_task_revision) is not int or expected_task_revision < 0
        ):
            raise ValueError("expected_task_revision must be a non-negative integer")
        return self._repository.suppress_dispatches_with_receipt(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
            reason_code=reason_code,
        )

    def resume_with_receipt(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_task_revision: int,
    ) -> dict[str, Any]:
        if type(expected_task_revision) is not int or expected_task_revision < 0:
            raise ValueError("expected_task_revision must be a non-negative integer")
        return self._repository.resume_suppressed_dispatches_with_receipt(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            expected_task_revision=expected_task_revision,
        )


__all__ = ["DispatchContinuationRetryableError", "ResearchDispatchService"]

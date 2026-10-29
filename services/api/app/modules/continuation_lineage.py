"""Follow committed continuation links without reopening completed execution."""

from dataclasses import dataclass
from typing import Any

from app.modules.continuation_responsibility import (
    ContinuationResponsibilityKind,
    ContinuationResponsibilityProof,
)


@dataclass(frozen=True)
class ContinuationBoundary:
    directive: Any
    proof: ContinuationResponsibilityProof | None = None
    first_successor_id: str | None = None
    first_selection_request_id: str | None = None


def prepare_lineage_boundary(service: Any, fence: Any) -> ContinuationBoundary:
    """Advance only unique, forward, scope-checked persisted replay links."""
    repository = service._repository
    current_id = fence.execution_decision_id
    visited: set[str] = set()
    hop_limit: int | None = None
    first_id = first_request = None
    while current_id not in visited:
        visited.add(current_id)
        directive = repository.prepare_execution_continuation(
            task_id=fence.task_id,
            owner_id=fence.owner_id,
            workspace_id=fence.workspace_id,
            completed_execution_decision_id=current_id,
            changed_by=fence.owner_id,
        )
        boundary = ContinuationBoundary(directive, None, first_id, first_request)
        if directive.action != "already_started":
            return boundary
        request_id = directive.selection_request_id
        if request_id != f"continuation:{current_id}":
            return _unproven(service, fence, boundary, "selection_replay_identity_missing")
        reader = getattr(repository, "lookup_selection_replay_evidence", None)
        if not callable(reader):
            return _unproven(service, fence, boundary, "selection_replay_reader_missing")
        evidence = reader(
            task_id=fence.task_id,
            owner_id=fence.owner_id,
            workspace_id=fence.workspace_id,
            selection_request_id=request_id,
        )
        replay = None if evidence is None else getattr(evidence, "replay", None)
        decision = None if replay is None else getattr(replay, "decision", None)
        reason = None if replay is None else getattr(replay, "reason_code", None)
        if reason is not None:
            proof = service._blocked_selection_user_proof(
                fence,
                selection_request_id=request_id,
                reason_code=str(reason),
            )
            return ContinuationBoundary(directive, proof, first_id, first_request)
        if decision is None:
            return _unproven(service, fence, boundary, "selection_replay_missing")
        task = service._load_continuation_task(fence)
        if task is None:
            return _unproven(service, fence, boundary, "successor_lineage_missing")
        if hop_limit is None:
            hop_limit = len(task.execution_decisions)
        if len(visited) > hop_limit:
            return _unproven(service, fence, boundary, "successor_lineage_changed")
        source = next(
            (row for row in task.execution_decisions if row.execution_decision_id == current_id),
            None,
        )
        successors = [
            row for row in task.execution_decisions if row.selection_request_id == request_id
        ]
        if (
            source is None
            or len(successors) != 1
            or successors[0].execution_decision_id != decision.execution_decision_id
            or successors[0].decision_sequence <= source.decision_sequence
            or service._value(source, "status") != "closed"
        ):
            return _unproven(service, fence, boundary, "successor_lineage_missing")
        successor = successors[0]
        if first_id is None:
            first_id, first_request = successor.execution_decision_id, request_id
        proof = service._successor_execution_proof_from_task(
            fence,
            task=task,
            successor_execution_decision_id=first_id,
            selection_request_id=first_request,
        )
        if proof.kind is not ContinuationResponsibilityKind.NO_PROOF:
            return ContinuationBoundary(directive, proof, first_id, first_request)
        if service._value(successor, "status") != "closed":
            return ContinuationBoundary(directive, proof, first_id, first_request)
        current_id = successor.execution_decision_id
    return _unproven(service, fence, boundary, "successor_lineage_cycle")


def _unproven(
    service: Any,
    fence: Any,
    boundary: ContinuationBoundary,
    reason: str,
) -> ContinuationBoundary:
    return ContinuationBoundary(
        boundary.directive,
        service._no_continuation_proof(fence, reason),
        boundary.first_successor_id,
        boundary.first_selection_request_id,
    )

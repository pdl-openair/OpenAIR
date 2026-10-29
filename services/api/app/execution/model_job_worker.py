"""Durable worker for user-triggered operations that may call a model."""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

from app.config import settings
from app.modules.model_job_queue import ModelJobLease, ModelJobQueueRepository
from app.modules.research_design_agent_revision import (
    ResearchDesignRevisionSaveUnavailable,
    ResearchDesignRevisionUserError,
    revise_research_design_with_agent,
)
from app.modules.research_execution_decision_service import (
    ExecutionPreflightBlocked,
)
from app.modules.research_execution_start_status import (
    ResearchExecutionInitialStartDenied,
)
from app.modules.research_orchestration_repository import (
    ResearchOrchestrationPersistenceError,
    ResearchOrchestrationRepository,
    TaskRevisionConflictError,
)
from app.modules.research_orchestration_resolver import ResolutionRejectedError
from app.modules.research_start_planner_budget import (
    research_start_planner_task_budget_seconds,
)

logger = logging.getLogger("openair.model_jobs")


class _LeaseKeeper:
    def __init__(
        self,
        repository: ModelJobQueueRepository,
        lease: ModelJobLease,
        *,
        lease_seconds: int,
    ) -> None:
        self._repository = repository
        self._lease = lease
        self._lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"model-job-lease-{lease.job_id}",
            daemon=True,
        )
        self.lost = False

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            self.lost = True

    def _run(self) -> None:
        renew_interval = max(1.0, min(10.0, self._lease_seconds / 3))
        while not self._stop.wait(renew_interval):
            try:
                if not self._repository.renew(
                    self._lease,
                    lease_seconds=self._lease_seconds,
                ):
                    self.lost = True
                    return
            except Exception:
                self.lost = True
                return


def model_job_lease_seconds() -> int:
    planner_budget = research_start_planner_task_budget_seconds()
    configured_worker_budget = float(getattr(settings, "worker_timeout_seconds", 600))
    return max(120, int(math.ceil(max(planner_budget, configured_worker_budget))) + 60)


def _run_execution_selection(lease: ModelJobLease) -> dict[str, Any]:
    from app.execution.research_start_worker_runtime import (
        _production_research_execution_decision_service,
    )

    repository = ResearchOrchestrationRepository()
    try:
        repository.load(
            task_id=lease.resource_id,
            owner_id=lease.owner_id,
            workspace_id=lease.workspace_id,
        )
    except ResearchOrchestrationPersistenceError as exc:
        if str(exc) != "invalid_research_orchestration_payload":
            raise
        try:
            repository.load_read_only(
                task_id=lease.resource_id,
                owner_id=lease.owner_id,
                workspace_id=lease.workspace_id,
            )
        except ResearchOrchestrationPersistenceError:
            raise exc
        raise ExecutionPreflightBlocked(
            "legacy_research_design_execution_authority_incomplete"
        ) from exc

    service = _production_research_execution_decision_service(
        repository
    )
    result = service.start_next(
        task_id=lease.resource_id,
        owner_id=lease.owner_id,
        workspace_id=lease.workspace_id,
        selection_request_id=lease.job_id,
        expected_task_revision=int(lease.expected_revision),
        invocation_context=(
            dict(lease.command_payload.get('invocationContext') or {})
        ),
        invocation_context_definition_id=(
            str(lease.command_payload.get('invocationContextDefinitionId'))
            if lease.command_payload.get('invocationContextDefinitionId')
            else None
        ),
    )
    return {
        "executionDecisionId": result.decision.execution_decision_id,
        "taskRevision": result.task.revision,
        "replayed": result.replayed,
    }


def _run_research_design_agent_revision(lease: ModelJobLease) -> dict[str, Any]:
    task = revise_research_design_with_agent(
        task_id=lease.resource_id,
        owner_id=lease.owner_id,
        workspace_id=lease.workspace_id,
        instruction=str(lease.command_payload.get("instruction") or ""),
        expected_task_revision=int(lease.expected_revision),
        request_id=lease.job_id,
    )
    return {"taskRevision": task.revision}


def _run_research_design_plan_revision(lease: ModelJobLease) -> dict[str, Any]:
    # Imported lazily to avoid coupling API startup to worker-only execution.
    from app.models.schemas import UpdateTaskPlanIn
    from app.routes.tasks import _revise_canonical_research_design

    body = UpdateTaskPlanIn.model_validate(lease.command_payload.get("plan"))
    updated = _revise_canonical_research_design(
        task_id=lease.resource_id,
        body=body,
        identity={
            "ownerId": lease.owner_id,
            "workspaceId": lease.workspace_id,
            "createdBy": lease.owner_id,
        },
        request_id=lease.job_id,
    )
    return {"taskRevision": int(updated.get("revision") or 0)}


def _run_agent_session_turn(lease: ModelJobLease) -> dict[str, Any]:
    from app.modules.agent_session_service import append_user_message

    session = append_user_message(
        session_id=lease.resource_id,
        owner_id=lease.owner_id,
        workspace_id=lease.workspace_id,
        content=str(lease.command_payload.get("content") or ""),
        request_id=lease.job_id,
    )
    return {"sessionStatus": session["status"], "updatedAt": session["updatedAt"]}


def _execute(lease: ModelJobLease) -> dict[str, Any]:
    handlers = {
        "execution_selection": _run_execution_selection,
        "research_design_agent_revision": _run_research_design_agent_revision,
        "research_design_plan_revision": _run_research_design_plan_revision,
        "agent_session_turn": _run_agent_session_turn,
    }
    try:
        handler = handlers[lease.job_kind]
    except KeyError as exc:
        raise RuntimeError("model_job_kind_unsupported") from exc
    return handler(lease)


def process_model_job_once(*, worker_id: str) -> bool:
    queue = ModelJobQueueRepository()
    try:
        queue.reconcile_confirmed_design_jobs()
    except Exception:
        logger.exception("confirmed design model-job reconciliation failed")
    lease_seconds = model_job_lease_seconds()
    lease = queue.claim(lease_owner=worker_id, lease_seconds=lease_seconds)
    if lease is None:
        return False

    keeper = _LeaseKeeper(queue, lease, lease_seconds=lease_seconds)
    keeper.start()
    try:
        from app.modules.provider_transport_evidence import provider_execution_parent

        with provider_execution_parent(
            kind="model_job",
            parent_id=lease.job_id,
            originating_authority=f"model_job:{lease.job_kind}",
        ):
            result = _execute(lease)
    except ExecutionPreflightBlocked as exc:
        if not keeper.lost:
            queue.finish(
                lease,
                state="blocked",
                result_payload={"reasonCode": exc.code, "facts": dict(exc.facts)},
                error_code=exc.code,
            )
    except ResearchExecutionInitialStartDenied as exc:
        if not keeper.lost:
            status = exc.start_status
            reason_code = (
                status.blockingReasonCode
                or status.failureReasonCode
                or "execution_start_not_allowed"
            )
            queue.finish(
                lease,
                state="blocked",
                result_payload={
                    "reasonCode": reason_code,
                    "facts": {"taskRevision": status.taskRevision},
                },
                error_code=reason_code,
            )
    except ResolutionRejectedError as exc:
        if not keeper.lost:
            queue.finish(
                lease,
                state="blocked",
                result_payload={"reasonCode": exc.code},
                error_code=exc.code,
            )
    except TaskRevisionConflictError:
        if not keeper.lost:
            queue.finish(lease, state="failed", error_code="task_revision_conflict")
    except ResearchDesignRevisionUserError as exc:
        if not keeper.lost:
            queue.finish(lease, state="failed", error_code=str(exc))
    except ResearchDesignRevisionSaveUnavailable as exc:
        if not keeper.lost:
            queue.finish(
                lease,
                state="failed",
                error_code=getattr(exc, "code", None) or str(exc),
                retryable=True,
            )
    except Exception:
        logger.exception(
            "model job failed jobKind=%s resourceId=%s jobId=%s",
            lease.job_kind,
            lease.resource_id,
            lease.job_id,
        )
        if not keeper.lost:
            queue.finish(
                lease,
                state="failed",
                error_code="model_job_system_failure",
                retryable=True,
            )
    else:
        if not keeper.lost:
            queue.finish(lease, state="succeeded", result_payload=result)
    finally:
        keeper.close()
    return True


__all__ = ["model_job_lease_seconds", "process_model_job_once"]

"""Fenced direct-DB runtime for durable research-start requests."""

from __future__ import annotations

from app.execution.research_start_processing import (
    PlannerProcessingError, observe_planning_response,
)

import json
import logging
import os
import threading
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Mapping, Protocol

from app.config import settings
from app.execution.research_start_provider_adapter import (
    ProviderContractError,
    ProviderKnownFailureError,
    ProviderTransportNotSubmittedError,
    ProviderTransportUncertainError,
)
from app.execution.research_start_provider_phase import ResearchStartProviderPhase
from app.modules.research_start_repository import (
    LeaseLostError,
    latest_usable_research_design_for_start_completion,
)
from app.modules.research_start_recovery_policy import PLANNER_HTTP_RECOVERY_POLICY
from app.modules.workflow_planning_admission import WorkflowPlanningAdmission

_logger = logging.getLogger("openair.research_start")

PLANNER_MAX_ATTEMPTS = 3



def _lifecycle_marker(
    event: str,
    *,
    request_id: str,
    worker_id: str,
    lease_epoch: int,
    phase: str,
    task_id: str | None = None,
    exception: BaseException | None = None,
) -> None:
    """Emit bounded, request-scoped lifecycle evidence without provider data."""
    payload: dict[str, object] = {
        "event": event,
        "requestId": request_id,
        "workerId": worker_id,
        "leaseEpoch": lease_epoch,
        "requestState": "leased",
        "phase": phase,
    }
    if task_id:
        payload["taskId"] = task_id
    if exception is not None:
        payload["exceptionType"] = type(exception).__name__
        payload["exceptionMessage"] = str(exception)[:500]
    _logger.info("research_start_lifecycle %s", json.dumps(payload, sort_keys=True))


class ResearchStartProvider(Protocol):
    def prepare_planner(
        self, context: Mapping[str, Any], selection: WorkflowPlanningAdmission | None,
        *, ordinal: int, prior_failure: str | None = None,
    ) -> Any: ...
    def execute_planner(self, prepared: Any) -> Any: ...


class ResearchStartOneShotProvider:
    """Thin W6A adapter. Preparation is pure; execution sends exactly once."""

    def prepare_planner(
        self, context: Mapping[str, Any], selection: WorkflowPlanningAdmission | None,
        *, ordinal: int, prior_failure: str | None = None,
    ) -> Any:
        from app.modules.research_start_planner import (
            ResearchStartPlannerUnavailable,
            prepare_planner_attempt,
        )

        try:
            return prepare_planner_attempt(
                context["intent"],
                selection,
                ordinal,
                prior_failure,
                definition_catalog=context.get("definitionCatalog"),
                workflow_planning_context=context.get("workflowPlanningContext"),
                planning_sources=context.get("planningSources"),
            )
        except ResearchStartPlannerUnavailable:
            error = ProviderKnownFailureError()
            error.failure_code = "research_start_planner_unavailable"
            raise error from None

    def execute_planner(self, prepared: Any) -> Any:
        if isinstance(prepared, Mapping) and prepared.get("planningSource") == "deterministic_fallback":
            return prepared
        from app.modules.research_start_planner import execute_planner_attempt

        return execute_planner_attempt(prepared)


class ResearchStartDeterministicTestProvider:
    """In-process provider for the isolated deterministic acceptance stack."""

    def __init__(self) -> None:
        if (
            (os.environ.get("OPENAIR_ENV") or "").strip().lower() != "test"
            or os.environ.get("OPENAIR_RESEARCH_START_TEST_FAKE_PROVIDER") != "1"
        ):
            raise ProviderContractError()

    def prepare_planner(
        self, context: Mapping[str, Any], selection: WorkflowPlanningAdmission | None,
        *, ordinal: int, prior_failure: str | None = None,
    ) -> dict[str, Any]:
        if ordinal != 1 or prior_failure is not None:
            raise ProviderContractError()
        return {"intent": dict(context["intent"]), "selection": selection}

    def execute_planner(self, prepared: Any) -> Mapping[str, Any]:
        if not isinstance(prepared, Mapping) or not isinstance(prepared.get("intent"), Mapping):
            raise ProviderContractError()
        from app.modules.research_start_planner import plan_research_start_draft

        intent = prepared["intent"]
        plan = plan_research_start_draft(
            raw_input=str(intent["input"]),
            source_page=str(intent.get("sourcePage") or "home"),
            selected_hint=intent.get("selectedHint"),
        )
        return plan.to_dict()


class ResearchStartLeaseKeeper:
    """Renew through fresh repository connections so a failed transaction cannot linger."""

    def __init__(
        self,
        repository_factory: Callable[[], Any],
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        renew_seconds: float,
        lease_seconds: int,
        renew_query_timeout_ms: int = 2000,
    ) -> None:
        self._repository_factory = repository_factory
        self._request_id = request_id
        self._worker_id = worker_id
        self._lease_epoch = lease_epoch
        self._renew_seconds = renew_seconds
        self._lease_seconds = lease_seconds
        self._renew_query_timeout_ms = renew_query_timeout_ms
        self._stop = threading.Event()
        self.lost = False
        self._thread = threading.Thread(target=self._renew, name=f"research-start-lease-{request_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=(self._renew_query_timeout_ms / 1000) + 0.5)
        if self._thread.is_alive():
            self.lost = True
            raise RuntimeError("research-start lease renewal thread did not stop within its database timeout")

    def _renew(self) -> None:
        while not self._stop.wait(self._renew_seconds):
            try:
                self._repository_factory().renew(
                    self._request_id,
                    self._worker_id,
                    self._lease_epoch,
                    lease_seconds=self._lease_seconds,
                    query_timeout_ms=self._renew_query_timeout_ms,
                )
            except Exception:
                self.lost = True
                return


def _attempt(context: Mapping[str, Any], phase: str, *, status: str = "succeeded", ordinal: int = 1) -> Mapping[str, Any] | None:
    return next(
        (
            value
            for value in context["attempts"]
            if value["phase"] == phase and value["status"] == status and value["ordinal"] == ordinal
        ),
        None,
    )


def _phase_attempts(context: Mapping[str, Any], phase: str) -> list[Mapping[str, Any]]:
    return [value for value in context["attempts"] if value["phase"] == phase]


def _failure_code_from_attempt(attempt: Mapping[str, Any]) -> str:
    result = attempt.get("result")
    return str(result.get("failureCode", "provider_known_failure")) if isinstance(result, Mapping) else "provider_known_failure"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    raise ProviderContractError()


def _execute_llm_with_one_pre_submission_retry(execute: Callable[[], Any]) -> Any:
    """Retry once only when transport proves no real Provider submission occurred."""
    for attempt in range(2):
        try:
            return execute()
        except ProviderTransportNotSubmittedError:
            if attempt == 1:
                raise
    raise AssertionError("unreachable")


def _resolve_known_planner_http_failure(
    repository: Any,
    context: Mapping[str, Any],
    error: ProviderKnownFailureError,
    attempt_id: str,
) -> bool:
    """Delegate eligible known HTTP failures to the durable recovery authority."""
    status_code = getattr(error, "status_code", None)
    if not PLANNER_HTTP_RECOVERY_POLICY.is_eligible_status(status_code):
        return False
    resolve = getattr(repository, "resolve_known_planner_http_failure", None)
    if not callable(resolve):
        return False
    try:
        disposition = resolve(
            str(context["requestId"]),
            str(context["workerId"]),
            int(context["leaseEpoch"]),
            attempt_id,
            failure_result=_failure_result(error),
            automatic_recovery_limit=PLANNER_HTTP_RECOVERY_POLICY.max_automatic_recoveries,
            terminal_user_message="We could not create a research design. Please try again later.",
        )
    except Exception as exc:
        _logger.info("planner known-http durable resolution failed: %s", type(exc).__name__)
        return False
    return disposition in {"recovery_queued", "terminal"}


def _workflow_planning_admission(context: Mapping[str, Any]) -> WorkflowPlanningAdmission | None:
    """Use only the user's explicit Workflow selection; free input uses the Planner."""
    intent = context.get("intent")
    if not isinstance(intent, Mapping):
        raise ValueError("research-start intent is invalid")
    selected = intent.get("requestedWorkflow")
    raw_input = str(intent.get("input") or "")
    if selected == "pvd_process_optimization_workflow":
        from app.modules.pvd_workflow_admission import pvd_workflow_planning_admission

        return pvd_workflow_planning_admission()
    if selected == "opentrajectory_research_workflow":
        from app.modules.opentrajectory_workflow_admission import opentrajectory_workflow_planning_admission

        return opentrajectory_workflow_planning_admission(raw_input)
    if selected == "cs_paper_reproduction_workflow":
        from app.modules.cs_reproduction_workflow_admission import cs_reproduction_workflow_planning_admission

        return cs_reproduction_workflow_planning_admission()
    if selected in {"materials_candidate_discovery_workflow", "materials_full_discovery_workflow"}:
        from app.modules.materials_workflow_admission import materials_workflow_planning_admission

        return materials_workflow_planning_admission(raw_input, workflow_id=selected)
    if selected is not None:
        raise ValueError("requested Workflow is not registered")
    return None

def _failure_code(error: Exception) -> str:
    return str(getattr(error, "failure_code", "provider_known_failure"))


def _failure_result(error: Exception) -> dict[str, Any]:
    result: dict[str, Any] = {"failureCode": _failure_code(error)}
    error_code = getattr(error, "safe_error_code", None)
    field_paths = tuple(getattr(error, "safe_field_paths", ()))
    if isinstance(error_code, str) and error_code:
        result["validatorDiagnostic"] = {
            "errorCode": error_code,
            "fieldPaths": [path for path in field_paths if isinstance(path, str)][:16],
        }
    diagnostic = getattr(error, "safe_diagnostic", None)
    if isinstance(diagnostic, Mapping) and diagnostic:
        result["providerDiagnostic"] = dict(diagnostic)
    transport_diagnostic = getattr(error, "safe_transport_diagnostic", None)
    if isinstance(transport_diagnostic, Mapping) and transport_diagnostic:
        result["transportDiagnostic"] = dict(transport_diagnostic)
    provider_diagnostic = getattr(error, "safe_provider_diagnostic", None)
    if isinstance(provider_diagnostic, Mapping) and provider_diagnostic:
        result["providerDiagnostic"] = dict(provider_diagnostic)
    planner_diagnostic = getattr(error, "safe_planner_diagnostic", None)
    if isinstance(planner_diagnostic, Mapping) and planner_diagnostic:
        result["plannerDiagnostic"] = dict(planner_diagnostic)
    processing_diagnostic = getattr(error, "safe_processing_diagnostic", None)
    if isinstance(processing_diagnostic, Mapping) and processing_diagnostic:
        result["processingDiagnostic"] = dict(processing_diagnostic)
        result["recoveryAction"] = "local_processing_repair_required"
    call_ref = getattr(error, "provider_call_ref", None)
    if isinstance(call_ref, str) and call_ref.strip():
        result["providerCallRef"] = call_ref.strip()[:240]
    return result


def _is_planner_retryable_failure(result: Mapping[str, Any]) -> bool:
    """Retry only when no usable planning response could be assembled.

    Model meaning is not judged here. Local noise has already been normalized
    by the Planner adapters; this boundary covers only an unreadable response
    or a response that selected no registered research work at all.
    """
    diagnostic = result.get("validatorDiagnostic")
    error_code = diagnostic.get("errorCode") if isinstance(diagnostic, Mapping) else None
    return bool(
        result.get("failureCode") == "provider_contract_invalid"
        and error_code in {
            "planner_response_json_invalid",
            "planner_no_registered_work",
            "provider_final_content_unavailable",
            "provider_call_identity_missing",
        }
    )


def _research_design_is_confirmable(task: Any) -> bool:
    return latest_usable_research_design_for_start_completion(
        getattr(task, "research_design_versions", ())
    ) is not None


def _research_design_coverage_feedback(task: Any) -> str:
    designs = tuple(getattr(task, "research_design_versions", ()))
    if not designs:
        uncovered_targets: list[dict[str, Any]] = []
    else:
        latest = max(designs, key=lambda row: row.version)
        coverage = latest.coverage_report
        uncovered_ids = set(coverage.uncovered_required_target_ids if coverage else ())
        expected_kinds = {
            "target_output": "target_output",
            "constraint": "constraint_compliance",
            "evidence_boundary": "evidence_boundary_compliance",
            "external_action_boundary": "external_action_guard",
            "risk_boundary": "risk_guard",
        }
        uncovered_targets = [
            {
                "sourcePath": target.source_path,
                "requiredCriterionKind": expected_kinds[target.source_type.value],
                "requiredArtifactTypes": list(target.required_artifact_types),
            }
            for target in (coverage.targets if coverage else ())
            if target.target_id in uncovered_ids
        ][:32]
    return json.dumps(
        {
            "failureCode": "provider_contract_invalid",
            "validatorDiagnostic": {
                "errorCode": "research_design_coverage_incomplete",
                "fieldPaths": ["/coverageClaimCandidates"],
            },
            "repairContext": {"uncoveredTargets": uncovered_targets},
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _fail_request(repository: Any, context: Mapping[str, Any], failure_code: str, user_message: str) -> None:
    _logger.warning(
        "research_start failed requestId=%s failureCode=%s message=%s",
        context.get("requestId"),
        failure_code,
        user_message,
    )
    repository.fail_request(
        context["requestId"], context["workerId"], context["leaseEpoch"],
        failure_code=failure_code,
        user_message=user_message,
    )


def process_research_start_claim(
    claim: Mapping[str, Any],
    *,
    repository: Any,
    provider: ResearchStartProvider,
    worker_id: str,
    keeper_factory: Callable[..., ResearchStartLeaseKeeper] = ResearchStartLeaseKeeper,
) -> None:
    """Process one already-claimed request without reaching the HTTP API."""
    request_id = str(claim["requestId"])
    lease_epoch = int(claim["leaseEpoch"])
    renewal_repository = getattr(repository, "for_lease_renewal", lambda: repository)
    keeper = keeper_factory(renewal_repository, request_id, worker_id, lease_epoch, 10, 60)
    keeper.start()
    from llm_core.token_usage import begin_usage_collection, end_usage_collection

    usage_bucket, usage_token = begin_usage_collection()
    owner_id = ""
    workspace_id: str | None = None
    reserved_task_id: str | None = None
    try:
        _lifecycle_marker(
            "POST_CLAIM_HANDLER_ENTER",
            request_id=request_id,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            phase="research_start",
        )
        context = repository.load_execution_context(request_id, worker_id, lease_epoch)
        context = {**context, "workerId": worker_id}
        owner_id = str(context.get("ownerId") or "")
        workspace_raw = context.get("workspaceId")
        workspace_id = str(workspace_raw) if workspace_raw else None
        reserved_raw = context.get("reservedTaskId")
        reserved_task_id = str(reserved_raw) if reserved_raw else None
        if keeper.lost:
            return

        selection = _workflow_planning_admission(context)
        if settings.research_orchestration_v1:
            _run_research_design_planner(repository, provider, context, keeper, selection)
            return

        _run_legacy_research_planner_compat(repository, provider, context, keeper, selection)
    except LeaseLostError as exc:
        _lifecycle_marker(
            "LEASE_LOST",
            request_id=request_id,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            phase="research_start",
            task_id=reserved_task_id,
            exception=exc,
        )
        return
    except Exception as exc:
        _lifecycle_marker(
            "POST_CLAIM_EXCEPTION",
            request_id=request_id,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            phase="research_start",
            task_id=reserved_task_id,
            exception=exc,
        )
        raise
    finally:
        try:
            if owner_id and usage_bucket:
                from app.modules.token_ledger_repository import flush_observed_usage

                flush_observed_usage(
                    user_id=owner_id,
                    source="research_start",
                    bucket=usage_bucket,
                    workspace_id=workspace_id,
                    task_id=reserved_task_id,
                )
        except Exception:  # noqa: BLE001 鈥?metering must not break claim finalization
            _logger.warning("research_start worker usage flush failed requestId=%s", request_id, exc_info=True)
        finally:
            end_usage_collection(usage_token)
        keeper.close()


_RESEARCH_START_EXECUTION_MODES = frozenset({
    "automatic_research",
    "stage_confirmation",
    "manual_step",
})


def _research_start_execution_mode(context: Mapping[str, Any]) -> str:
    intent = context.get("intent")
    mode = intent.get("executionMode") if isinstance(intent, Mapping) else None
    mode = str(mode or "stage_confirmation")
    if mode not in _RESEARCH_START_EXECUTION_MODES:
        raise ValueError("invalid research-start execution mode")
    return mode


def _apply_research_start_execution_policy(context: Mapping[str, Any]) -> str:
    mode = _research_start_execution_mode(context)
    if mode == "stage_confirmation":
        # ResearchDesign tasks are created with this canonical default. Avoid a
        # new DB dependency for legacy/default starts that do not need a change.
        return mode
    from app.modules.collaboration_repository import CollaborationRepository

    policy = CollaborationRepository().set_task_execution_policy(
        task_id=str(context["reservedTaskId"]),
        owner_id=str(context["ownerId"]),
        workspace_id=str(context["workspaceId"]),
        mode=mode,
        changed_by=str(context["createdBy"]),
    )
    return str(policy["currentMode"])


def _production_research_execution_decision_service(orchestration_repository: Any) -> Any:
    from app.modules.research_execution_decision_service import (
        ResearchExecutionDecisionService,
        build_production_resolution_facts,
    )
    from app.modules.research_orchestration_resolver import ResearchOrchestrationResolver
    from app.modules.registry_snapshot_repository import snapshot_from_envelope

    class _RepositorySnapshotArchive:
        def get(self, content_hash: str) -> Any:
            return snapshot_from_envelope(
                orchestration_repository.load_registry_snapshot(content_hash)
            )

        def put(self, _snapshot: Any) -> None:
            raise RuntimeError("selection cannot mutate the Registry snapshot archive")


    return ResearchExecutionDecisionService(
        repository=orchestration_repository,
        resolver=ResearchOrchestrationResolver(_RepositorySnapshotArchive()),
        facts_source=build_production_resolution_facts,
    )


def _bind_definition_source_to_task(source: Any, task: Any) -> Any:
    """Reopen DefinitionSource on the Task-frozen registry snapshot.

    Accept/reserve freezes ``registrySnapshotRef`` on the Task. Worker planning
    must honor that authority instead of putting a newer current registry
    snapshot, otherwise ``plan()`` fails closed at preflight before any Planner
    attempt is recorded.
    """
    ref = getattr(task, "registry_snapshot_ref", None)
    if ref is None:
        return source
    snapshot_ref = (
        ref.model_dump(mode="json", by_alias=True)
        if hasattr(ref, "model_dump")
        else {
            "schemaVersion": int(ref["schemaVersion"] if isinstance(ref, Mapping) else ref.schema_version),
            "contentHash": str(ref["contentHash"] if isinstance(ref, Mapping) else ref.content_hash),
        }
    )
    if not hasattr(source, "snapshot_ref"):
        return source
    if source.snapshot_ref() == snapshot_ref:
        return source
    from app.modules.production_definition_source import ProductionDefinitionSource

    return ProductionDefinitionSource(snapshot_ref=snapshot_ref)


def _continue_automatic_research(
    *,
    context: Mapping[str, Any],
    execution_mode: str,
    orchestration_repository: Any,
    design_service: Any,
) -> None:
    if execution_mode != "automatic_research":
        return

    task = orchestration_repository.load(
        task_id=str(context["reservedTaskId"]),
        owner_id=str(context["ownerId"]),
        workspace_id=str(context["workspaceId"]),
    )
    design = latest_usable_research_design_for_start_completion(
        task.research_design_versions
    )
    if design is None:
        return
    owned_planning_attempt_ids = {
        row.planning_attempt_id
        for row in task.planning_attempts
        if row.request_id in {
            str(context["requestId"]),
            f"{context['requestId']}:semantic-repair:2",
        }
        and str(getattr(row.status, "value", row.status)) == "completed"
    }
    coverage = getattr(design, "coverage_report", None)
    if (
        coverage is None
        or getattr(coverage, "planning_attempt_id", None) not in owned_planning_attempt_ids
    ):
        return

    design_status = str(getattr(design.status, "value", design.status))
    if design_status != "confirmed":
        return

    task = orchestration_repository.load(
        task_id=str(context["reservedTaskId"]),
        owner_id=str(context["ownerId"]),
        workspace_id=str(context["workspaceId"]),
    )
    selection_request_id = f"research-start:auto-start:{context['requestId']}"
    if any(
        row.selection_request_id == selection_request_id
        for row in task.execution_decisions
    ):
        return

    from app.modules.research_execution_decision_service import (
        ExecutionPreflightBlocked,
    )

    try:
        from app.modules.provider_transport_evidence import provider_execution_parent

        with provider_execution_parent(
            kind="research_start",
            parent_id=str(context["requestId"]),
            originating_authority="research_start:next_task_selection",
            only_if_absent=True,
        ):
            _production_research_execution_decision_service(orchestration_repository).start_next(
                task_id=task.task_id,
                owner_id=str(context["ownerId"]),
                workspace_id=str(context["workspaceId"]),
                selection_request_id=selection_request_id,
                expected_task_revision=task.revision,
            )
    except ExecutionPreflightBlocked:
        # Do not turn a durable confirmed Design into a failed Research Start.
        # A later start attempt must use the confirmed work order and
        # re-evaluate current dependency, input and runtime facts.
        return


def _run_legacy_research_planner_compat(
    repository: Any,
    provider: ResearchStartProvider,
    context: Mapping[str, Any],
    keeper: Any,
    selection: WorkflowPlanningAdmission | None,
    *,
    persisted_result: Mapping[str, Any] | None = None,
) -> None:
    """Preserve the pre-v1 durable-worker contract for legacy Planner results."""
    if context.get("catalogBinding") is not None:
        repository.fail_request(
            context["requestId"], context["workerId"], context["leaseEpoch"],
            failure_code="catalog_execution_requires_orchestration_v1",
            user_message="The selected catalog capability cannot be executed by the legacy research path.",
        )
        return

    planner_result = dict(persisted_result) if persisted_result is not None else None
    if planner_result is None:
        planner_successes = [
            value for value in _phase_attempts(context, "research_planner") if value["status"] == "succeeded"
        ]
        if planner_successes:
            planner_result = _mapping(
                max(planner_successes, key=lambda value: int(value["ordinal"])).get("result")
            )
        else:
            planner_result = _run_planner(repository, provider, context, keeper, selection)
            if planner_result is None:
                return
    if keeper.lost:
        return

    from app.modules.research_start_task import build_research_start_draft_task_payload

    payload = build_research_start_draft_task_payload(
        task_id=context["reservedTaskId"], artifact_id=context["reservedArtifactId"],
        owner_id=context["ownerId"], workspace_id=context["workspaceId"], created_by=context["createdBy"],
        goal_summary=str(context["intent"]["input"]), planner_plan_override=planner_result,
        planning_selection=selection, source_page=str(context["intent"].get("sourcePage") or "home"),
        selected_hint=context["intent"].get("selectedHint"),
    )
    repository.finalize_task_request(
        context["requestId"], context["workerId"], context["leaseEpoch"],
        task=payload["task"], artifact=payload["artifact"],
        result={
            "outcome": "research_task",
            "sessionId": context["sessionId"],
            "taskId": context["reservedTaskId"],
        },
        user_message=(
            selection.user_facing_summary
            if selection is not None
            else "Research design is ready to confirm."
        ),
    )


def _persisted_planner_result_lineage(
    context: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any] | None]:
    successes = [
        value for value in _phase_attempts(context, "research_planner") if value["status"] == "succeeded"
    ]
    if not successes:
        return "none", None
    result = _mapping(max(successes, key=lambda value: int(value["ordinal"])).get("result"))

    # A v1.2 Planner success is persisted with provider lineage evidence. The
    # presence of either v1.2-only field is enough to prevent legacy fallback;
    # missing/blank/malformed provider identity must fail closed instead.
    if "providerCallRef" in result or "proposal" in result:
        provider_call_ref = result.get("providerCallRef")
        if not isinstance(provider_call_ref, str) or not provider_call_ref.strip():
            return "invalid_v1", result
        if "proposal" in result and not isinstance(result.get("proposal"), Mapping):
            return "invalid_v1", result
        return "v1", result

    # Legacy reuse is a positive contract: real pre-v1 persisted Planner
    # results are serialized ResearchStartPlan payloads. Keep the historical
    # opaque {"plan": ...} compatibility shape used by the legacy runtime seam,
    # but do not treat arbitrary unknown mappings as legacy.
    from app.modules.research_start_planner import ResearchStartPlannerError, hydrate_research_start_plan

    try:
        hydrate_research_start_plan(result)
    except ResearchStartPlannerError:
        if set(result) == {"plan"}:
            return "legacy", result
        return "ambiguous", result
    return "legacy", result


def _run_research_design_planner(
    repository: Any,
    provider: ResearchStartProvider,
    context: Mapping[str, Any],
    keeper: Any,
    selection: WorkflowPlanningAdmission | None,
) -> None:
    persisted_lineage, persisted_result = _persisted_planner_result_lineage(context)
    if persisted_lineage == "legacy":
        assert persisted_result is not None
        _run_legacy_research_planner_compat(
            repository, provider, context, keeper, selection, persisted_result=persisted_result
        )
        return
    if persisted_lineage in {"invalid_v1", "ambiguous"}:
        _fail_request(
            repository,
            context,
            "provider_contract_invalid",
            "The durable research design receipt could not be verified.",
        )
        return
    persisted_v1_success = persisted_lineage == "v1"

    from app.modules.production_definition_source import ProductionDefinitionSource
    from app.modules.research_design_service import (
        PlannerProviderResult,
        PlannerTransportUnknown,
        ResearchDesignProposalError,
        ResearchDesignService,
        ResearchDesignServiceError,
    )
    from app.modules.research_orchestration_repository import (
        PersistenceBackendUnsupportedError,
        ResearchOrchestrationRepository,
    )
    from app.modules.research_start_task import ensure_research_design_task

    from app.modules.catalog_execution_binding import binding_from_public

    try:
        binding_from_public(context.get("catalogBinding"))
    except ValueError:
        repository.fail_request(
            context["requestId"], context["workerId"], context["leaseEpoch"],
            failure_code="catalog_execution_binding_invalid",
            user_message="The selected catalog capability could not be verified for execution.",
        )
        return

    try:
        source = ProductionDefinitionSource(planning_only=True)
    except Exception:
        if settings.persistence_backend == "postgres":
            raise
        if persisted_v1_success:
            _fail_request(
                repository,
                context,
                "provider_contract_invalid",
                "The durable research design receipt could not be verified.",
            )
            return
        _run_legacy_research_planner_compat(repository, provider, context, keeper, selection)
        return
    try:
        task = ensure_research_design_task(
            task_id=str(context["reservedTaskId"]),
            goal_summary=str(context["intent"]["input"]),
            title=None,
            identity={
                "ownerId": str(context["ownerId"]),
                "workspaceId": str(context["workspaceId"]),
                "createdBy": str(context["createdBy"]),
            },
            definition_source=source,
        )
    except PersistenceBackendUnsupportedError:
        # Generic starts retain the
        # pre-v1 deterministic Planner contract when durable v1.2 storage is
        # unavailable, but an already-persisted v1.2 success must never be
        # downgraded to legacy merely because durable storage is unavailable.
        if persisted_v1_success:
            _fail_request(
                repository,
                context,
                "provider_contract_invalid",
                "The durable research design receipt could not be verified.",
            )
            return
        _run_legacy_research_planner_compat(repository, provider, context, keeper, selection)
        return
    source = _bind_definition_source_to_task(source, task)
    orchestration_repository = ResearchOrchestrationRepository()
    service = ResearchDesignService(
        repository=orchestration_repository,
        definition_source=source,
    )
    execution_mode = _apply_research_start_execution_policy(context)
    _continue_automatic_research(
        context=context,
        execution_mode=execution_mode,
        orchestration_repository=orchestration_repository,
        design_service=service,
    )
    reconcile_committed = getattr(repository, "reconcile_committed_design_request", None)
    authoritative_reconciliation_available = callable(reconcile_committed)
    if authoritative_reconciliation_available:
        reconciled = reconcile_committed(
            context["requestId"],
            context["workerId"],
            context["leaseEpoch"],
            user_message=str(
                selection.user_facing_summary if selection is not None else None
                or "Research design is ready to confirm."
            ),
        )
        if reconciled is not None:
            return
    planner_context = dict(context)
    from app.modules.workflow_planning import workflow_planning_bundle

    workflow_bundle = workflow_planning_bundle(
        selection=selection,
        raw_goal=str(context["intent"]["input"]),
        definition_source=source,
    )
    if workflow_bundle is None:
        planner_context["definitionCatalog"] = source.planner_catalog_for_research_design(None)
    else:
        task = ensure_research_design_task(
            task_id=task.task_id,
            goal_summary=str(context["intent"]["input"]),
            title=None,
            identity={
                "ownerId": str(context["ownerId"]),
                "workspaceId": str(context["workspaceId"]),
                "createdBy": str(context["createdBy"]),
            },
            definition_source=source,
            skill_pack=str(workflow_bundle.admission["skillPack"]),
            workflow_admission=workflow_bundle.admission,
        )
        planner_context["definitionCatalog"] = list(workflow_bundle.definition_catalog)
        planner_context["workflowPlanningContext"] = dict(workflow_bundle.planning_context)

    planning_source_refs = context["intent"].get("planningSources") or []
    if planning_source_refs:
        from app.modules.research_package_asset import (
            ResearchPackageConflictError,
            ResearchPackageNotFoundError,
        )
        from app.modules.research_planning_sources import (
            resolve_research_package_planning_sources,
        )

        try:
            planner_context["planningSources"] = resolve_research_package_planning_sources(
                planning_source_refs,
                owner_id=str(context["ownerId"]),
                workspace_id=str(context["workspaceId"]),
            )
        except (ValueError, ResearchPackageNotFoundError, ResearchPackageConflictError):
            repository.fail_request(
                context["requestId"], context["workerId"], context["leaseEpoch"],
                failure_code="planning_source_invalid",
                user_message="A selected prior research source could not be verified for planning.",
            )
            return
    else:
        planner_context["planningSources"] = []
    prior_failure: str | None = None

    # Every attempt has the same authority and receives a fresh planning call.
    # The durable phase ordinal is the only retry state; no repair state exists.
    for ordinal in range(1, PLANNER_MAX_ATTEMPTS + 1):
        existing = next(
            (
                row for row in _phase_attempts(context, "research_planner")
                if row["ordinal"] == ordinal
            ),
            None,
        )
        if existing is not None:
            status = existing["status"]
            if status in {"started", "unknown"}:
                return
            if status == "failed":
                result = existing.get("result") if isinstance(existing.get("result"), Mapping) else {}
                if ordinal < PLANNER_MAX_ATTEMPTS and _is_planner_retryable_failure(result):
                    prior_failure = json.dumps(result, separators=(",", ":"), sort_keys=True)
                    continue
                repository.fail_request(
                    context["requestId"], context["workerId"], context["leaseEpoch"],
                    failure_code=str(result.get("failureCode") or "provider_known_failure"),
                    user_message="We could not create a research design. Please review the research request.",
                )
                return
            # Existing started/unknown attempts are never replayed, and an
            # existing succeeded attempt may complete only through the atomic
            # authoritative receipt reconciliation already attempted above.
            # There is deliberately no Task-only fallback here.
            repository.fail_request(
                context["requestId"], context["workerId"], context["leaseEpoch"],
                failure_code="provider_contract_invalid",
                user_message="The compiled research design did not satisfy the active contract.",
            )
            return

        begun: Mapping[str, Any] | None = None
        provider_result: PlannerProviderResult | None = None
        attempt_finished = False
        phase_owner: ResearchStartProviderPhase | None = None

        def finish_begun_failure(result: Mapping[str, Any]) -> str | None:
            nonlocal attempt_finished
            if phase_owner is None or begun is None or attempt_finished or keeper.lost:
                return None
            disposition = phase_owner.fail(result)
            attempt_finished = True
            return disposition

        def responsibility_transferred(disposition: str | None) -> bool:
            return disposition in {"external_result_unknown", "task_handoff", "lease_lost"}

        try:
            prepared = provider.prepare_planner(
                planner_context,
                selection,
                ordinal=ordinal,
                prior_failure=prior_failure,
            )

            def execute_once(_idempotency_key: str) -> PlannerProviderResult:
                nonlocal begun, provider_result, attempt_finished, phase_owner
                phase_owner = ResearchStartProviderPhase(
                    repository=repository,
                    request_id=str(context["requestId"]),
                    worker_id=str(context["workerId"]),
                    lease_epoch=int(context["leaseEpoch"]),
                    phase="research_planner",
                    ordinal=ordinal,
                    lease_lost=lambda: bool(keeper.lost),
                )
                external_call_permitted = phase_owner.begin()
                begun = {"attemptId": phase_owner.attempt_id}
                if not external_call_permitted:
                    raise ProviderKnownFailureError()
                try:
                    def persist_response(attempt, raw, metadata):
                        try:
                            repository.record_attempt_response(
                                context["requestId"], context["workerId"], context["leaseEpoch"],
                                begun["attemptId"],
                                raw=raw,
                                metadata={**dict(metadata), "providerExecutionId": begun["attemptId"]},
                            )
                        except LeaseLostError:
                            raise
                        except Exception as exc:
                            raise PlannerProcessingError("response_evidence_persistence", exc) from exc

                    with observe_planning_response(persist_response):
                        result = phase_owner.execute(lambda: provider.execute_planner(prepared))
                except ProviderTransportUncertainError as exc:
                    phase_owner.unknown(_failure_result(exc))
                    attempt_finished = True
                    raise PlannerTransportUnknown("Planner transport result is unknown") from exc
                except ProviderKnownFailureError as exc:
                    if (
                        not keeper.lost
                        and not PLANNER_HTTP_RECOVERY_POLICY.is_eligible_status(
                            getattr(exc, "status_code", None)
                        )
                    ):
                        phase_owner.fail(_failure_result(exc))
                        attempt_finished = True
                    raise
                if not isinstance(result, PlannerProviderResult):
                    error = ProviderContractError()
                    if not keeper.lost:
                        phase_owner.fail(_failure_result(error))
                        attempt_finished = True
                    raise error
                provider_result = result
                return result

            current_task = orchestration_repository.load(
                task_id=task.task_id,
                owner_id=str(context["ownerId"]),
                workspace_id=str(context["workspaceId"]),
            )
            design_request_id = str(context["requestId"])
            if ordinal > 1:
                design_request_id = f"{design_request_id}:planner-attempt:{ordinal}"
            planned = service.plan(
                task_id=task.task_id,
                owner_id=str(context["ownerId"]),
                workspace_id=str(context["workspaceId"]),
                request_id=design_request_id,
                expected_task_revision=current_task.revision,
                provider=execute_once,
                user_input=str(context["intent"]["input"]),
                user_input_source_ref=str(context["requestId"]),
            )
            if keeper.lost:
                return
            if begun is None or provider_result is None:
                raise ResearchDesignServiceError("Planner attempt completed without physical-call evidence")
            if not _research_design_is_confirmable(planned):
                failure = json.loads(_research_design_coverage_feedback(planned))
                if responsibility_transferred(finish_begun_failure(failure)):
                    return
                repository.fail_request(
                    context["requestId"], context["workerId"], context["leaseEpoch"],
                    failure_code="provider_contract_invalid",
                    user_message="The compiled research design did not satisfy the active planning contract.",
                )
                return
            if phase_owner is None:
                raise ResearchDesignServiceError("Planner ownership was not established")
            phase_owner.succeed(
                {
                    "providerCallRef": provider_result.provider_call_ref,
                    "proposal": dict(provider_result.proposal),
                    "planningSourceUsages": [
                        row.model_dump(mode="json", by_alias=True)
                        for row in provider_result.planning_source_usages
                    ],
                }
            )
            attempt_finished = True
        except PlannerTransportUnknown:
            return
        except ProviderContractError as exc:
            failure = _failure_result(exc)
            if responsibility_transferred(finish_begun_failure(failure)):
                return
            if ordinal < PLANNER_MAX_ATTEMPTS and _is_planner_retryable_failure(failure):
                prior_failure = json.dumps(failure, separators=(",", ":"), sort_keys=True)
                continue
            repository.fail_request(
                context["requestId"], context["workerId"], context["leaseEpoch"],
                failure_code=_failure_code(exc),
                user_message="研究方案准备暂时未完成。本次规划未通过校验，输入和已收到的记录已保留。",
            )
            return
        except ResearchDesignProposalError as exc:
            error = PlannerProcessingError("design_materialization", exc)
            if responsibility_transferred(finish_begun_failure(_failure_result(error))):
                return
            repository.fail_request(
                context["requestId"], context["workerId"], context["leaseEpoch"],
                failure_code=error.failure_code,
                user_message="研究方案准备暂时未完成。系统已保留本次输入和可用记录，当前需要修复系统处理问题。",
            )
            return
        except ProviderKnownFailureError as exc:
            if (
                begun is not None
                and not attempt_finished
                and _resolve_known_planner_http_failure(
                    repository, context, exc, str(begun["attemptId"])
                )
            ):
                attempt_finished = True
                _logger.warning(
                    "research_start planner scheduled durable recovery requestId=%s status=%s",
                    context.get("requestId"),
                    getattr(exc, "status_code", None),
                )
                return
            if responsibility_transferred(finish_begun_failure(_failure_result(exc))):
                return
            repository.fail_request(
                context["requestId"], context["workerId"], context["leaseEpoch"],
                failure_code=_failure_code(exc),
                user_message="We could not create a research design. Please try again later.",
            )
            return
        except LeaseLostError:
            raise
        except (PlannerProcessingError, ResearchDesignServiceError, ValueError, KeyError, TypeError) as exc:
            error = exc if isinstance(exc, PlannerProcessingError) else PlannerProcessingError("design_materialization", exc)
            if responsibility_transferred(finish_begun_failure(_failure_result(error))):
                return
            repository.fail_request(
                context["requestId"], context["workerId"], context["leaseEpoch"],
                failure_code=error.failure_code,
                user_message="研究方案准备暂时未完成。系统已保留本次输入和可用记录，当前需要修复系统处理问题。",
            )
            return

        if keeper.lost:
            return
        _continue_automatic_research(
            context=context,
            execution_mode=execution_mode,
            orchestration_repository=orchestration_repository,
            design_service=service,
        )
        user_message = str(
            selection.user_facing_summary if selection is not None else None
            or "Research design is ready to confirm."
        )
        finalize_authoritative = getattr(
            repository,
            "finalize_from_authoritative_receipt",
            None,
        )
        finalized: Mapping[str, Any] | None = None
        if callable(finalize_authoritative):
            finalized = finalize_authoritative(
                context["requestId"],
                context["workerId"],
                context["leaseEpoch"],
                user_message=user_message,
            )
            finalized_successfully = (
                isinstance(finalized, Mapping)
                and finalized.get("finalized") is True
            )
        elif authoritative_reconciliation_available:
            # Compatibility for narrow test repositories.  Production always
            # exposes finalize_from_authoritative_receipt; this fallback still
            # performs the second authoritative read and never uses Task-only
            # completion.
            finalized_successfully = reconcile_committed(
                context["requestId"],
                context["workerId"],
                context["leaseEpoch"],
                user_message=user_message,
            ) is not None
        else:
            finalized_successfully = False

        if finalized_successfully:
            return
        if (
            isinstance(finalized, Mapping)
            and finalized.get("closed") is True
        ):
            # Production finalization fails closed in the same transaction that
            # revalidates the authoritative receipt.  Do not open a second
            # transaction or overwrite the canonical failure state.
            return
        repository.fail_request(
            context["requestId"],
            context["workerId"],
            context["leaseEpoch"],
            failure_code="provider_contract_invalid",
            user_message="The durable research design receipt could not be verified.",
        )
        return

def _run_planner(
    repository: Any,
    provider: ResearchStartProvider,
    context: Mapping[str, Any],
    keeper: Any,
    selection: WorkflowPlanningAdmission | None,
) -> dict[str, Any] | None:
    request_id, worker_id, epoch = context["requestId"], context["workerId"], context["leaseEpoch"]
    for ordinal, prior_failure in ((1, None), (2, "provider_contract_invalid")):
        existing = next(
            (value for value in _phase_attempts(context, "research_planner") if value["ordinal"] == ordinal),
            None,
        )
        if existing is not None:
            status = existing["status"]
            if status == "succeeded":
                return _mapping(existing.get("result"))
            if status in {"started", "unknown"}:
                return None
            code = _failure_code_from_attempt(existing)
            if ordinal == 1 and code == "provider_contract_invalid":
                continue
            _fail_request(repository, context, code, "We could not create a research draft. Please try again later.")
            return None
        try:
            prepared = provider.prepare_planner(context, selection, ordinal=ordinal, prior_failure=prior_failure)
        except ProviderKnownFailureError as error:
            _fail_request(
                repository, context, _failure_code(error),
                "We could not create a research draft. Please try again later.",
            )
            return None
        phase_owner = ResearchStartProviderPhase(
            repository=repository,
            request_id=str(request_id),
            worker_id=str(worker_id),
            lease_epoch=int(epoch),
            phase="research_planner",
            ordinal=ordinal,
            lease_lost=lambda: bool(keeper.lost),
        )
        if not phase_owner.begin():
            return None
        try:
            result = _mapping(phase_owner.execute(lambda: provider.execute_planner(prepared)))
        except ProviderTransportUncertainError as exc:
            phase_owner.unknown(_failure_result(exc))
            return None
        except ProviderKnownFailureError as error:
            if keeper.lost:
                return None
            code = _failure_code(error)
            if _resolve_known_planner_http_failure(
                repository, context, error, str(phase_owner.attempt_id)
            ):
                return None
            disposition = phase_owner.fail(_failure_result(error))
            if disposition in {"external_result_unknown", "task_handoff"}:
                return None
            if isinstance(error, ProviderContractError) and ordinal == 1:
                continue
            repository.fail_request(request_id, worker_id, epoch, failure_code=code, user_message="We could not create a research draft. Please try again later.")
            return None
        if keeper.lost:
            return None
        phase_owner.succeed(result)
        return result
    return None

"""One formal internal tool command under its original Dispatch owner."""

from collections.abc import Callable, Mapping
from typing import Any

from app.execution.durable_skill_runtime import execute_production_durable_skill
from app.execution.research_input_contract_gate import InputContractGateFailure
from app.execution.research_internal_skill_call import InternalSkillCallError
from app.execution.research_internal_skill_call_budget import InternalCallBudgetError
from app.execution.research_internal_skill_call_preflight import preflight_internal_skill_call
from app.execution.research_internal_skill_call_processing import claim_internal_processing
from app.execution.research_internal_skill_call_reconciliation import (
    reconcile_internal_admission_failure,
)
from app.execution.research_internal_skill_call_response import preserve_internal_runtime_response
from app.execution.research_internal_skill_call_store import (
    InternalExecutionOwner,
    authorize_internal_provider_io,
    begin_internal_skill_call,
    finish_internal_skill_call,
    internal_call_checkpoint_body,
    internal_call_checkpoint_kind,
    reserve_internal_skill_call,
)
from app.modules.artifact_write import artifact_write_service
from app.modules.external_execution_checkpoint import ExternalExecutionCheckpointRepository


def execute_internal_skill_command(
    repository: Any,
    *,
    owner: InternalExecutionOwner,
    binding_ref: Mapping[str, Any],
    parameters: Mapping[str, Any],
    scope_check: Callable[[Mapping[str, Any], Any], None],
    checkpoints: Any = None,
    storage: Any = None,
) -> Mapping[str, Any]:
    """Reserve, prepare, authorize once, execute and preserve the actual receipt.

    Tool failure/unknown/pending are feedback to the original executor. This
    entry creates no new Task, WorkUnit, Decision or parent completion.
    """
    checkpoints = checkpoints or ExternalExecutionCheckpointRepository()
    storage = storage or artifact_write_service

    def preflight(request, *, task, snapshot):
        return preflight_internal_skill_call(
            request, task=task, snapshot=snapshot, scope_check=scope_check
        )

    prepared = reserve_internal_skill_call(
        repository, owner=owner, binding_ref=binding_ref, parameters=parameters, preflight=preflight
    )
    with claim_internal_processing(repository, prepared.call.capability_call_id) as claimed:
        if not claimed:
            return {
                "state": "running",
                "capabilityCallId": prepared.call.capability_call_id,
                "summary": "The original tool execution or receipt processing is active.",
            }
        return _execute_reserved_internal_tool(
            repository, owner, prepared, preflight, checkpoints, storage
        )


def _execute_reserved_internal_tool(repository, owner, prepared, preflight, checkpoints, storage):
    checkpoint = checkpoints.get(capability_call_id=prepared.call.capability_call_id)
    response = None
    known_not_submitted = False
    if (
        checkpoint is not None
        and checkpoint.phase == "prepared"
        and checkpoint.execution_kind == "provider_http"
        and prepared.call.status.value == "running"
    ):
        response, known_not_submitted = reconcile_internal_admission_failure(
            repository,
            owner=owner,
            prepared=prepared,
            checkpoint=checkpoint,
            error=InternalSkillCallError("internal_skill_provider_execution_recovery"),
        )
        if response is None:
            return {
                "state": "running",
                "capabilityCallId": prepared.call.capability_call_id,
                "summary": "The original tool execution needs physical attempt evidence.",
            }
    if response is None and (checkpoint is None or checkpoint.phase == "prepared"):
        task = repository.load_read_only(
            task_id=owner.task_id, owner_id=owner.owner_id, workspace_id=owner.workspace_id
        )
        frozen = prepared.call.internal_call_input_snapshot
        snapshot = repository.load_registry_snapshot(frozen.registry_snapshot_hash)
        try:
            runtime = preflight(prepared.request, task=task, snapshot=snapshot)
        except (InputContractGateFailure, ValueError) as exc:
            response = {
                "state": "failed",
                "summary": "The tool input or action authority is unavailable.",
                "failureClassification": "internal_skill_pre_io_rejected",
                "reasonCode": getattr(exc, "code", "internal_skill_pre_io_rejected"),
            }
            runtime = None
            known_not_submitted = True
        checkpoint = checkpoints.prepare(
            capability_call_id=prepared.call.capability_call_id,
            dispatch_id=owner.dispatch_id,
            execution_kind=internal_call_checkpoint_kind(prepared),
            provider_id="direct_skill_runtime",
            adapter=prepared.request["skillId"],
            request_body=internal_call_checkpoint_body(prepared),
        )
        if response is None:
            response, pending, known_not_submitted = _execute_or_read_internal_tool(
                repository,
                owner,
                prepared,
                runtime,
                checkpoints,
            )
            if pending:
                return {
                    "state": "running",
                    "capabilityCallId": prepared.call.capability_call_id,
                    "summary": "The original tool execution has no readable receipt yet.",
                }
    elif response is None and checkpoint.phase == "submitted":
        response, known_not_submitted = reconcile_internal_admission_failure(
            repository,
            owner=owner,
            prepared=prepared,
            checkpoint=checkpoint,
            error=InternalSkillCallError("internal_skill_provider_execution_recovery"),
        )
        if response is None:
            # No receipt or active transport is not proof of terminal failure.
            return {
                "state": "running",
                "capabilityCallId": prepared.call.capability_call_id,
                "summary": "The original tool execution is pending; no action was repeated.",
            }
    elif (
        response is None
        and checkpoint.phase == "response_received"
        and checkpoint.response_kind != "dispatch_completed"
    ):
        # Re-enter only result processing. Existing adapter/Provider checkpoint
        # code consumes the saved physical response; begin grants no second action.
        task = repository.load_read_only(
            task_id=owner.task_id,
            owner_id=owner.owner_id,
            workspace_id=owner.workspace_id,
        )
        frozen = prepared.call.internal_call_input_snapshot
        runtime = preflight(
            prepared.request,
            task=task,
            snapshot=repository.load_registry_snapshot(frozen.registry_snapshot_hash),
        )
        response, pending, known_not_submitted = _execute_or_read_internal_tool(
            repository,
            owner,
            prepared,
            runtime,
            checkpoints,
            recovery_only=True,
        )
        if pending:
            return {
                "state": "running",
                "capabilityCallId": prepared.call.capability_call_id,
                "summary": "The original tool receipt processing remains pending.",
            }
    result, response, receipt = preserve_internal_runtime_response(
        checkpoints=checkpoints,
        storage=storage,
        prepared=prepared,
        dispatch_id=owner.dispatch_id,
        response=response,
        known_not_submitted=known_not_submitted,
    )
    finish_internal_skill_call(repository, owner=owner, result=result, response_hash=receipt)
    return {
        **response,
        "capabilityCallId": prepared.call.capability_call_id,
        "resultId": result.result_id,
    }


def _execute_or_read_internal_tool(
    repository, owner, prepared, runtime, checkpoints, recovery_only=False
):
    claimed = False
    admission_denial = None

    def claim_adapter():
        nonlocal claimed, admission_denial
        try:
            claimed = begin_internal_skill_call(repository, owner=owner, prepared=prepared).execute
        except (InternalSkillCallError, InternalCallBudgetError) as exc:
            admission_denial = exc
            raise
        return claimed

    def provider_authorization(attempt):
        nonlocal admission_denial
        try:
            return authorize_internal_provider_io(
                repository,
                owner=owner,
                child_call_id=prepared.call.capability_call_id,
                attempt=attempt,
            )
        except (InternalSkillCallError, InternalCallBudgetError) as exc:
            admission_denial = exc
            raise

    kind = runtime["runtimeKind"]
    if kind != "external_adapter" and not recovery_only:
        begun = begin_internal_skill_call(repository, owner=owner, prepared=prepared)
        if not begun.execute:
            return None, True, False
        prepared = begun.prepared
        claimed = True
    request = {
        **prepared.request,
        "parameters": dict(runtime["parameters"]),
        "bindingInputEvidence": runtime["bindingInputEvidence"],
        "attemptNumber": prepared.call.attempt_number,
        "providerEvidence": {
            "capabilityCallId": prepared.call.capability_call_id,
            "attemptNumber": prepared.call.attempt_number,
        },
        "executionCheckpointId": prepared.call.capability_call_id,
        "_internalAdapterIoAuthorization": claim_adapter,
        "_internalProviderIoAuthorization": provider_authorization,
    }
    try:
        response = execute_production_durable_skill(request, prepared_runtime=dict(runtime))
    except (InternalSkillCallError, InternalCallBudgetError) as exc:
        admission_denial = exc
        response = None
    current = checkpoints.get(capability_call_id=prepared.call.capability_call_id)
    if admission_denial is not None:
        response, not_submitted = reconcile_internal_admission_failure(
            repository, owner=owner, prepared=prepared, checkpoint=current, error=admission_denial
        )
        if response is None:
            return None, True, False
        return response, False, not_submitted
    if (
        current.phase == "prepared"
        and current.execution_kind == "provider_http"
        and response.get("state") == "failed"
    ):
        reconciled, not_submitted = reconcile_internal_admission_failure(
            repository,
            owner=owner,
            prepared=prepared,
            checkpoint=current,
            error=InternalSkillCallError(
                str(response.get("failureClassification") or "internal_skill_provider_failed")
            ),
        )
        if reconciled is None or not not_submitted:
            return None, True, False
        return (
            {**response, "providerAttemptEvidence": reconciled["providerAttemptEvidence"]},
            False,
            True,
        )
    # A losing concurrent adapter caller must not overwrite a live winner with
    # its local result-unavailable wrapper. A positive staged response is reusable.
    if not claimed and current.phase == "submitted":
        return None, True, False
    if not claimed and current.phase == "prepared":
        raise InternalSkillCallError("internal_skill_tool_did_not_claim_submission")
    return response, False, False

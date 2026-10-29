"""Internal routes for worker → API server callbacks.

These endpoints are not exposed to the frontend. Startup and claim use the
global worker control-plane credential; task/run data-plane operations use
execution-scoped tokens when per-task authentication is enabled.

Endpoints
---------
POST /internal/worker/result        — Final execution result
POST /internal/worker/stream-event  — Single streaming event
POST /internal/worker/claim         — Warm worker long-polls for its next turn
GET  /internal/worker/task/{id}     — Worker fetches latest task state
GET  /internal/worker/containers    — List active worker containers
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.config import settings
from app.execution.worker_callback import get_callback_registry
from app.execution.durable_orchestrator_bridge import build_durable_orchestration_plan
from app.execution.durable_multiagent_orchestrator import DurableMultiAgentOrchestrator, ExternalResultUnknown
from app.execution.durable_skill_runtime import execute_durable_skill
from app.execution.durable_orchestrator_provider import (
    execute_prepared_durable_orchestration,
    prepare_durable_orchestration,
)
from app.execution.durable_orchestrator_runtime import (
    ArtifactContractValidationError,
    FrozenOrchestrationExecutionRequest,
    frozen_artifact_contract_from_snapshot,
    validate_frozen_artifact_content,
)
from app.execution.openai_responses_provider import (
    ProviderConfigurationError,
    ProviderContractError,
    ProviderExecutionError,
    ProviderRejectedRequestError,
    ProviderTransportUncertainError,
)
from app.models.research_external_result import (
    EvidenceCaptureRequest,
    EvidenceKind,
    EvidenceLineage,
    FailureClass,
    RejectedResponseDiagnosis,
    SafeValidatorDiagnostic,
)
from app.execution.worker_pool import get_worker_pool
from app.modules.durable_execution import DurableExecutionError, DurableExecutionRepository, serialize_run
from app.modules.artifact_write import ArtifactStorageError
from app.modules.collaboration_repository import CollaborationRepository
from app.modules.research_external_evidence_repository import (
    ExternalEvidenceRecorder,
    ResearchExternalEvidenceRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


def _capture_provider_contract_evidence(
    *,
    durable: DurableExecutionRepository,
    prepared: Any,
    unit_request: dict[str, Any],
    error: ProviderContractError,
) -> dict[str, Any]:
    """Write bounded diagnostics and raw bytes without exposing either to API output."""
    context = unit_request.get("providerEvidence")
    if not isinstance(context, dict):
        return {"errorCode": "provider_response_contract_invalid", "nextAction": "review_provider_contract"}
    diagnosis, local_validator_failure = _provider_contract_diagnosis(error)
    if diagnosis is None:
        return {"errorCode": "provider_response_contract_invalid", "nextAction": "review_provider_contract"}
    first = diagnosis.diagnostics[0] if diagnosis.diagnostics else None
    raw = b"" if local_validator_failure else getattr(error, "evidence_body", b"")
    if not isinstance(raw, bytes):
        raw = b""
    metadata = {
        "adapter": prepared.adapter,
        "diagnostics": [item.model_dump(mode="json", by_alias=True) for item in diagnosis.diagnostics],
        "finishReason": diagnosis.normalized_finish_reason,
        "providerId": prepared.provider_id,
    }
    request = EvidenceCaptureRequest(
        evidence_id=f"evidence_{uuid4().hex}",
        lineage=EvidenceLineage(
            task_id=unit_request["taskId"],
            execution_decision_id=context["executionDecisionId"],
            work_unit_id=context["workUnitId"],
            capability_call_id=context["capabilityCallId"],
            dispatch_id=context["dispatchId"],
            attempt_number=context["attemptNumber"],
        ),
        owner_id=unit_request["ownerId"],
        workspace_id=unit_request["workspaceId"],
        provider_id=prepared.provider_id,
        adapter=prepared.adapter,
        evidence_kind=EvidenceKind.REJECTED_RESPONSE,
        failure_class=FailureClass.RESPONSE_CONTRACT,
        normalized_finish_reason=diagnosis.normalized_finish_reason,
        diagnostics=diagnosis.diagnostics,
        canonical_metadata_fingerprint=hashlib.sha256(
            json.dumps(metadata, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        ).hexdigest(),
        canonical_payload_fingerprint=context["canonicalPayloadFingerprint"],
        reconciliation_evidence_refs=(),
    )
    recorder = ExternalEvidenceRecorder()
    envelope = recorder.record(
        request,
        raw,
        total_byte_length=getattr(error, "total_byte_length", len(raw)),
    )
    ResearchExternalEvidenceRepository(connection_factory=durable._connect, recorder=recorder).put(envelope)
    return {
        "errorCode": first.error_code if first else "provider_response_contract_invalid",
        "path": first.field_path if first else "",
        "expectedType": first.expected_type if first else None,
        "actualType": first.actual_type if first else None,
        "nextAction": "review_provider_contract",
    }


def _provider_contract_diagnosis(error: ProviderContractError) -> tuple[RejectedResponseDiagnosis | None, bool]:
    diagnosis = getattr(error, "diagnosis", None)
    if isinstance(diagnosis, RejectedResponseDiagnosis):
        return diagnosis, False
    error_code = getattr(error, "safe_error_code", None)
    field_paths = getattr(error, "safe_field_paths", ())
    if not isinstance(error_code, str) or not isinstance(field_paths, tuple) or not field_paths:
        return None, False
    diagnostics = []
    for path in field_paths:
        if not isinstance(path, str):
            return None, False
        try:
            diagnostics.append(SafeValidatorDiagnostic(
                error_code=error_code,
                category="frozen_artifact_schema",
                json_pointer=path,
                field_path=path.lstrip("/").replace("/", "."),
                validator_keyword="frozen_schema",
            ))
        except ValueError:
            return None, False
    return RejectedResponseDiagnosis(
        category="frozen_artifact_schema",
        normalized_finish_reason="stop",
        diagnostics=tuple(diagnostics),
    ), True


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _verify_worker_token(
    token: str | None,
    expected_task_id: str | None = None,
    *,
    expected_owner_id: str | None = None,
    expected_execution_id: str | None = None,
    expected_execution_epoch: str | None = None,
) -> None:
    """Validate worker authentication with strict control/data-plane separation.

    Global worker credentials are accepted only by endpoints with no task
    context (for example startup-contract and container observability).  When
    per-task mode is enabled, every task-context request must present a signed
    execution token and that token must match the server-side callback authority.
    """
    if token and token.startswith("wt."):
        if not settings.worker_per_task_token or not expected_task_id:
            raise HTTPException(status_code=403, detail="Invalid or unbound worker token")
        from app.execution.worker_tokens import parse_worker_token, verify_worker_token

        claims = parse_worker_token(token)
        if claims is None:
            raise HTTPException(status_code=403, detail="Invalid or unbound worker token")

        if expected_execution_id is not None or expected_execution_epoch is not None:
            authority_task_id = str(expected_task_id or "")
            authority_owner_id = str(expected_owner_id or "")
            authority_execution_id = str(expected_execution_id or "")
            authority_execution_epoch = str(expected_execution_epoch or "")
        else:
            try:
                authority = get_callback_registry().get_authority(claims.execution_id)
            except Exception as exc:
                logger.exception("worker callback authority lookup failed")
                raise HTTPException(
                    status_code=503,
                    detail="Worker callback authority unavailable",
                ) from exc
            if authority is None:
                raise HTTPException(status_code=403, detail="Unknown or expired worker execution")
            authority_task_id = authority.task_id
            authority_owner_id = authority.owner_id
            authority_execution_id = authority.execution_id
            authority_execution_epoch = authority.execution_epoch

        if authority_task_id != expected_task_id:
            raise HTTPException(status_code=403, detail="Worker token task mismatch")
        if expected_owner_id is not None and authority_owner_id != expected_owner_id:
            raise HTTPException(status_code=403, detail="Worker token owner mismatch")
        if not verify_worker_token(
            token,
            expected_task_id=authority_task_id,
            expected_owner_id=authority_owner_id,
            expected_execution_id=authority_execution_id,
            expected_execution_epoch=authority_execution_epoch,
        ):
            raise HTTPException(status_code=403, detail="Invalid or stale worker execution token")
        return

    # A global secret is a control-plane credential.  In per-task mode it must
    # never authorize result, stream, task, artifact, or claim endpoints.
    if settings.worker_per_task_token and expected_task_id is not None:
        raise HTTPException(status_code=403, detail="Global worker token forbidden for task endpoint")
    expected = settings.worker_auth_token
    if not expected:
        raise HTTPException(status_code=403, detail="Worker auth token not configured")
    if not token or token != expected:
        raise HTTPException(status_code=403, detail="Invalid worker token")


def _verify_durable_run_token(
    repository: DurableExecutionRepository,
    token: str | None,
    *,
    run_id: str,
    worker_id: str,
    lease_epoch: int,
) -> None:
    """Authenticate one run-scoped data-plane operation.

    The global worker secret is only a control-plane credential.  In per-task
    mode, the path/body identity is resolved against the durable run row and a
    signed token must match task, owner, run, and the exact lease epoch.
    """
    if not settings.worker_per_task_token:
        _verify_worker_token(token)
        return
    if not token or not token.startswith("wt."):
        raise HTTPException(status_code=403, detail="Global worker token forbidden for durable run endpoint")
    try:
        authority = repository.load_active_worker_authority(run_id, worker_id, lease_epoch)
    except Exception as exc:
        logger.exception("durable run callback authority lookup failed run_id=%s", run_id)
        raise HTTPException(status_code=503, detail="Durable run callback authority unavailable") from exc
    if authority is None:
        raise HTTPException(status_code=403, detail="Unknown or stale durable run execution")
    _verify_worker_token(
        token,
        expected_task_id=authority["task_id"],
        expected_owner_id=authority["owner_id"],
        expected_execution_id=authority["run_id"],
        expected_execution_epoch=str(authority["lease_epoch"]),
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class WorkerResultIn(BaseModel):
    execution_id: str
    reply: str = ""
    session_id: str | None = None
    status: str = "completed"  # completed | failed | timeout
    error: str | None = None
    runtime: str = "claude_code"
    usage: dict[str, Any] | None = None  # normalized token usage (S03-14-02)


class WorkerStreamEventIn(BaseModel):
    execution_id: str
    event: dict[str, Any]


class WorkerClaimIn(BaseModel):
    """Body for ``POST /internal/worker/claim`` (warm worker long-poll)."""

    task_id: str
    owner_id: str = ""
    container_id: str = ""


class DurableClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workerId: str
    notBefore: AwareDatetime | None = None


class DurableLeaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workerId: str
    leaseEpoch: int


class DurableCallbackIn(DurableLeaseIn):
    type: str
    payload: dict[str, Any] = {}


class DurableUploadIntentIn(DurableLeaseIn):
    objectKey: str | None = None
    expectedSize: int | None = None
    expectedSha256: str | None = None
    capabilityCallId: str | None = None
    presentation: dict[str, Any]


class DurableUploadConfirmIn(DurableLeaseIn):
    # Object readability, length, and checksum are deliberately not accepted
    # from a worker.  The API reads the object storage itself on confirmation.
    pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _durable_error(exc: DurableExecutionError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _durable_claim_not_before(requested: datetime | None) -> datetime | None:
    raw = os.environ.get("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", "").strip()
    configured: datetime | None = None
    if raw:
        try:
            configured = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="durable_claim_cutoff_invalid") from exc
        if configured.tzinfo is None:
            raise HTTPException(status_code=503, detail="durable_claim_cutoff_invalid")
        configured = configured.astimezone(timezone.utc)
    elif settings.is_production:
        raise HTTPException(status_code=503, detail="durable_claim_cutoff_required")

    normalized_requested = requested.astimezone(timezone.utc) if requested is not None else None
    if configured is None:
        return normalized_requested
    if normalized_requested is None:
        return configured
    return max(configured, normalized_requested)


@router.post("/worker/startup-contract")
def worker_startup_contract(
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    """Expose the non-secret canonical execution contract to authenticated workers."""
    _verify_worker_token(x_openair_worker_token)
    return {"contract": settings.execution_startup_contract()}


@router.post("/durable-runs/claim")
def durable_claim(
    body: DurableClaimIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    """Atomically lease one queued run.  Workers never open a DB connection."""
    _verify_worker_token(x_openair_worker_token)
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    run = repository.claim(
        body.workerId,
        settings.durable_run_lease_seconds,
        not_before=_durable_claim_not_before(body.notBefore),
    )
    if not run:
        return {"run": None}
    response = {"run": serialize_run(run), "inputSnapshot": run["input_snapshot"]}
    if settings.worker_per_task_token:
        from app.execution.worker_tokens import mint_worker_token

        response["workerToken"] = mint_worker_token(
            str(run["task_id"]),
            str(run["owner_id"]),
            str(run["run_id"]),
            str(run["lease_epoch"]),
        )
    return response


@router.post("/durable-runs/{run_id}/renew")
def durable_renew(
    run_id: str,
    body: DurableLeaseIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    _verify_durable_run_token(
        repository, x_openair_worker_token, run_id=run_id,
        worker_id=body.workerId, lease_epoch=body.leaseEpoch,
    )
    try:
        run = repository.renew(run_id, body.workerId, body.leaseEpoch, settings.durable_run_lease_seconds)
    except DurableExecutionError as exc:
        _durable_error(exc)
    return serialize_run(run)


@router.post("/durable-runs/{run_id}/orchestration-plan")
def durable_orchestration_plan(
    run_id: str,
    body: DurableLeaseIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    """Create a durable routing plan; this endpoint never invokes a provider."""
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    _verify_durable_run_token(
        repository, x_openair_worker_token, run_id=run_id,
        worker_id=body.workerId, lease_epoch=body.leaseEpoch,
    )
    try:
        run = repository.read_orchestration_context(run_id, body.workerId, body.leaseEpoch)
        plan = build_durable_orchestration_plan(run)
        stored_plan, replayed = repository.record_orchestration_plan(
            run_id, body.workerId, body.leaseEpoch, plan,
        )
    except DurableExecutionError as exc:
        _durable_error(exc)
    return {"plan": stored_plan, "replayed": replayed}


@router.post("/durable-runs/{run_id}/orchestrate")
def durable_orchestrate(
    run_id: str,
    body: DurableLeaseIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    """Reject execution over HTTP; orchestration is worker-process-only."""
    if not settings.worker_per_task_token:
        _verify_worker_token(x_openair_worker_token)
        raise HTTPException(status_code=409, detail="durable_orchestration_worker_process_only")
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    _verify_durable_run_token(
        repository, x_openair_worker_token, run_id=run_id,
        worker_id=body.workerId, lease_epoch=body.leaseEpoch,
    )
    raise HTTPException(status_code=409, detail="durable_orchestration_worker_process_only")


def _execute_durable_orchestration_in_worker(
    run_id: str,
    body: DurableLeaseIn,
) -> dict[str, Any]:
    """Execute the existing fenced durable state machine inside a trusted worker."""
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    try:
        run, plan = repository.read_orchestration_execution_context(run_id, body.workerId, body.leaseEpoch)
    except DurableExecutionError as exc:
        _durable_error(exc)

    work_units = plan.get("workUnits") if isinstance(plan, dict) else None
    # Every confirmed plan, including a one-Agent plan, goes through the
    # durable specialist path.  The legacy single-provider fallback cannot
    # prove work-unit ownership or an inter-Agent handoff.
    if isinstance(work_units, list):
        collaboration = CollaborationRepository(repository)
        prepared_units: dict[str, Any] = {}

        def frozen_request_for_unit(unit_request: dict[str, Any]) -> FrozenOrchestrationExecutionRequest:
            policies = plan.get("providerPolicyByAgent")
            provider_policy = (
                dict(policies.get(unit_request["agentId"]) or {})
                if isinstance(policies, dict)
                else {}
            )
            unit_plan = {
                **plan,
                "selectedAgentId": unit_request["agentId"],
                "selectedSkillIds": unit_request["selectedSkillIds"],
                "selectedProvider": provider_policy.get("defaultProvider", plan.get("selectedProvider")),
                "providerPolicy": provider_policy or plan.get("providerPolicy"),
                "artifactContract": unit_request["artifactContract"],
                "artifactContractSchemaVersion": unit_request["artifactContractSchemaVersion"],
                "artifactRequiredFields": unit_request["artifactRequiredFields"],
                "artifactSemanticVersion": unit_request["artifactSemanticVersion"],
                "artifactSchemaDigest": unit_request["artifactSchemaDigest"],
                "artifactSchema": unit_request["artifactSchema"],
                "artifactSchemaRef": unit_request["artifactSchemaRef"],
            }
            unit_snapshot = {
                "goalSummary": unit_request["frozenGoal"],
                "creationPlan": {
                    "goalSummary": unit_request["frozenGoal"],
                    "clarifications": unit_request["frozenConstraints"],
                },
                "activeWorkUnit": {
                    "unitKey": unit_request["unitKey"],
                    "agentId": unit_request["agentId"],
                    "kind": unit_request["kind"],
                    "objective": unit_request["objective"],
                    "stageKey": unit_request["stageKey"],
                    "substageKey": unit_request["substageKey"],
                    "selectedSkillIds": unit_request["selectedSkillIds"],
                    "inputArtifactRefs": unit_request["inputArtifactRefs"],
                    "handoffSummaries": unit_request["handoffSummaries"],
                    "contextEnvelope": unit_request["contextEnvelope"],
                    "phase": unit_request.get("phase"),
                    "reviewedAgentId": unit_request.get("reviewedAgentId"),
                    "reviewContract": unit_request.get("reviewContract"),
                    "reviewedArtifactContract": unit_request.get("reviewedArtifactContract"),
                    "reviewMaterials": unit_request.get("reviewMaterials"),
                    "artifactContract": unit_request["artifactContract"],
                    "artifactContractSchemaVersion": unit_request["artifactContractSchemaVersion"],
                    "artifactRequiredFields": unit_request["artifactRequiredFields"],
                    "artifactSemanticVersion": unit_request["artifactSemanticVersion"],
                    "artifactSchemaDigest": unit_request["artifactSchemaDigest"],
                    "artifactSchema": unit_request["artifactSchema"],
                    "artifactSchemaRef": unit_request["artifactSchemaRef"],
                },
            }
            return FrozenOrchestrationExecutionRequest(
                runId=run["run_id"], taskId=run["task_id"], ownerId=run["owner_id"], workspaceId=run["workspace_id"],
                orchestrationPlan=unit_plan, frozenInputSnapshot=unit_snapshot,
                selectedAgentId=unit_request["agentId"], selectedSkillIds=tuple(unit_request["selectedSkillIds"]),
                logicalProviderId=unit_plan["selectedProvider"], modelProfile=unit_request["modelMode"],
                artifactContract=unit_request["artifactContract"],
                artifactContractSchemaVersion=unit_request["artifactContractSchemaVersion"],
                artifactRequiredFields=tuple(unit_request["artifactRequiredFields"]),
                artifactSemanticVersion=unit_request["artifactSemanticVersion"],
                artifactSchemaDigest=unit_request["artifactSchemaDigest"],
                artifactSchema=unit_request["artifactSchema"],
                artifactSchemaRef=unit_request["artifactSchemaRef"],
            )

        def preflight_agent_unit(unit_request: dict[str, Any]) -> dict[str, Any] | None:
            try:
                prepared_units[unit_request["unitKey"]] = prepare_durable_orchestration(frozen_request_for_unit(unit_request))
            except ProviderConfigurationError:
                return {
                    "summary": "当前研究能力配置不可用，本项研究尚未执行。",
                    "failureClassification": "provider_configuration",
                }
            except ProviderExecutionError:
                return {
                    "summary": "研究能力准备失败，本项研究尚未执行。",
                    "failureClassification": "provider_configuration",
                }
            return None

        def execute_agent_unit(unit_request: dict[str, Any]) -> dict[str, Any]:
            prepared = prepared_units.pop(unit_request["unitKey"], None)
            if prepared is None:
                return {
                    "state": "failed",
                    "summary": "当前研究能力配置不可用，本项研究尚未执行。",
                    "failureClassification": "provider_configuration",
                }
            try:
                # Transport uncertainty is one real submission fact.  Never hide
                # a second Provider submission inside this synchronous adapter;
                # durable recovery owns any later decision to submit again.
                result = execute_prepared_durable_orchestration(
                    prepared,
                    provider_evidence=unit_request.get("providerEvidence"),
                    owner_id=unit_request.get("ownerId"),
                    workspace_id=unit_request.get("workspaceId"),
                    task_id=unit_request.get("taskId"),
                )
            except ProviderTransportUncertainError as exc:
                raise ExternalResultUnknown("外部执行结果未知") from exc
            except ProviderConfigurationError:
                return {
                    "state": "failed",
                    "summary": "当前研究能力配置不可用，本项研究尚未执行。",
                    "failureClassification": "provider_configuration",
                }
            except ProviderRejectedRequestError:
                return {
                    "state": "failed",
                    "summary": "研究请求被外部服务明确拒绝，未形成研究结果。",
                    "failureClassification": "provider_rejected_request",
                }
            except ProviderContractError as exc:
                safe_diagnosis = _capture_provider_contract_evidence(
                    durable=repository,
                    prepared=prepared,
                    unit_request=unit_request,
                    error=exc,
                )
                return {
                    "state": "failed",
                    "summary": "外部模型已返回结果，但结果未通过结构化合同校验。",
                    "failureClassification": "provider_response_contract_invalid",
                    "safeDiagnosis": safe_diagnosis,
                }
            except ProviderExecutionError:
                return {
                    "state": "failed",
                    "summary": "模型执行发生已知内部失败，本项研究尚未形成可接受结果。",
                    "failureClassification": "provider_execution_invalid",
                }
            skill_request = result.get("skillRequest") if isinstance(result, dict) else None
            if skill_request is not None:
                return {
                    "state": "succeeded",
                    "summary": "专业智能体 已请求一项待核验的研究能力。",
                    "skillRequest": skill_request,
                }
            artifacts = result.get("artifacts") if isinstance(result, dict) else None
            artifact = artifacts[0] if isinstance(artifacts, list) and len(artifacts) == 1 else None
            presentation = artifact.get("presentation") if isinstance(artifact, dict) else None
            if not isinstance(artifact, dict) or not isinstance(presentation, dict):
                return {"state": "failed", "summary": "本项研究未形成可确认产物。"}
            if unit_request.get("phase") == "review_artifact":
                try:
                    content = base64.b64decode(artifact["contentBase64"], validate=True)
                    review_verdict = json.loads(content.decode("utf-8"))
                except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    return {
                        "state": "failed",
                        "summary": "Independent reviewer returned an invalid structured conclusion.",
                        "failureClassification": "provider_response_contract_invalid",
                    }
                if not isinstance(review_verdict, dict):
                    return {
                        "state": "failed",
                        "summary": "Independent reviewer returned an invalid structured conclusion.",
                        "failureClassification": "provider_response_contract_invalid",
                    }
                # The provider returns a transport artifact; reviewer output is never a research artifact.
                return review_verdict
            return {
                "state": "succeeded",
                "summary": str(presentation.get("title") or "专业智能体 已完成本项研究。"),
                "artifactPayload": artifact,
                "artifactContractSnapshot": {
                    "artifactContract": unit_request["artifactContract"],
                    "artifactSemanticVersion": unit_request["artifactSemanticVersion"],
                    "artifactSchemaDigest": unit_request["artifactSchemaDigest"],
                    "artifactSchema": unit_request["artifactSchema"],
                    "artifactSchemaRef": unit_request["artifactSchemaRef"],
                },
            }

        def persist_artifact(call_id: str, unit_result: dict[str, Any]) -> str:
            artifact = unit_result.get("artifactPayload")
            if not isinstance(artifact, dict):
                raise DurableExecutionError(422, "collaboration_artifact_payload_required")
            try:
                content = base64.b64decode(artifact["contentBase64"], validate=True)
            except (KeyError, TypeError, ValueError) as exc:
                raise DurableExecutionError(422, "collaboration_artifact_payload_invalid") from exc
            try:
                validate_frozen_artifact_content(
                    frozen_artifact_contract_from_snapshot(unit_result.get("artifactContractSnapshot")), content,
                )
            except (ArtifactContractValidationError, ValueError) as exc:
                raise DurableExecutionError(422, "collaboration_artifact_contract_invalid") from exc
            return repository.persist_collaboration_artifact(
                run_id,
                call_id,
                body.workerId,
                body.leaseEpoch,
                content=content,
                presentation=artifact.get("presentation"),
                summary=str(unit_result.get("summary") or "专业智能体 已完成本项研究。"),
                related_skill_id=unit_result.get("relatedSkillId"),
                artifact_state=(
                    "partial_success"
                    if unit_result.get("artifactState") == "partial_success"
                    else "needs_review"
                ),
            )

        try:
            outcome = DurableMultiAgentOrchestrator(
                collaboration=collaboration,
                execute_agent_unit=execute_agent_unit,
                persist_artifact=persist_artifact,
                execute_skill_unit=execute_durable_skill,
                preflight_agent_unit=preflight_agent_unit,
            ).run(
                frozen_run=run,
                persisted_plan=plan,
                worker_id=body.workerId,
                lease_epoch=body.leaseEpoch,
            )
        except ArtifactStorageError:
            terminal = repository.fail_artifact_storage(
                run_id,
                body.workerId,
                body.leaseEpoch,
            )
            return {
                "knownFailure": "artifact_storage_failed_after_provider_result",
                "run": serialize_run(terminal),
            }
        if outcome["state"] == "external_result_unknown":
            terminal = repository.callback(
                run_id,
                body.workerId,
                body.leaseEpoch,
                "abandoned",
                {"failureClassification": "external_result_unknown"},
            )
            return {"knownFailure": "external_result_unknown", "run": serialize_run(terminal)}
        if outcome["state"] == "failed":
            terminal = repository.callback(
                run_id,
                body.workerId,
                body.leaseEpoch,
                "failed",
                {"failureClassification": "multiagent_execution_failed"},
            )
            return {"knownFailure": "multiagent_execution_failed", "run": serialize_run(terminal)}
        if outcome["state"] == "blocked":
            terminal = repository.callback(
                run_id,
                body.workerId,
                body.leaseEpoch,
                "failed",
                {
                    "failureClassification": "no_authorized_specialist",
                    "planBlock": str(plan.get("planBlock") or "no_authorized_specialist"),
                },
            )
            return {"knownFailure": "no_authorized_specialist", "run": serialize_run(terminal)}
        if outcome["state"] == "awaiting_human":
            paused = repository.get_run(run_id, run["owner_id"], run["workspace_id"])
            if not paused or paused.get("state") != "awaiting_human":
                paused = collaboration.pause_for_artifact_review(
                    run_id=run_id,
                    owner_id=run["owner_id"],
                    workspace_id=run["workspace_id"],
                    worker_id=body.workerId,
                    fence_epoch=body.leaseEpoch,
                )["run"]
            return {"knownPause": "awaiting_human", "run": serialize_run(paused)}
        if outcome["state"] == "stopped_by_user":
            return {"knownFailure": "stopped_by_user"}
        return {"artifacts": [], "multiAgent": True, **outcome}

    try:
        blocked = repository.callback(
            run_id,
            body.workerId,
            body.leaseEpoch,
            "failed",
            {"failureClassification": "missing_frozen_specialist_plan"},
        )
    except DurableExecutionError as exc:
        _durable_error(exc)
    return {"knownFailure": "missing_frozen_specialist_plan", "run": serialize_run(blocked)}


@router.post("/durable-runs/{run_id}/callback")
def durable_callback(
    run_id: str,
    body: DurableCallbackIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    _verify_durable_run_token(
        repository, x_openair_worker_token, run_id=run_id,
        worker_id=body.workerId, lease_epoch=body.leaseEpoch,
    )
    try:
        run = repository.callback(run_id, body.workerId, body.leaseEpoch, body.type, body.payload)
    except DurableExecutionError as exc:
        _durable_error(exc)
    return serialize_run(run)


@router.post("/durable-runs/{run_id}/artifacts/request-upload")
def durable_request_upload(
    run_id: str,
    body: DurableUploadIntentIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    _verify_durable_run_token(
        repository, x_openair_worker_token, run_id=run_id,
        worker_id=body.workerId, lease_epoch=body.leaseEpoch,
    )
    try:
        from app.modules.minio_client import current_minio_storage_scope

        storage_scope = current_minio_storage_scope()
        upload = repository.request_upload(
            run_id, body.workerId, body.leaseEpoch, body.objectKey, body.expectedSize, body.expectedSha256,
            body.capabilityCallId, body.presentation, storage_scope=storage_scope,
        )
    except DurableExecutionError as exc:
        _durable_error(exc)
    try:
        from app.modules.minio_client import presigned_put_object

        upload_url = presigned_put_object(upload["object_key"], storage_scope=storage_scope)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="artifact_upload_storage_unavailable") from exc
    return {
        "uploadId": upload["upload_id"],
        "state": upload["state"],
        "objectKey": upload["object_key"],
        "storageScope": upload.get("storage_scope"),
        "uploadUrl": upload_url,
    }


@router.post("/durable-runs/{run_id}/artifacts/{upload_id}/confirm-upload")
def durable_confirm_upload(
    run_id: str,
    upload_id: str,
    body: DurableUploadConfirmIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    if settings.persistence_backend != "postgres":
        raise HTTPException(status_code=503, detail="durable_execution_requires_postgres")
    repository = DurableExecutionRepository()
    _verify_durable_run_token(
        repository, x_openair_worker_token, run_id=run_id,
        worker_id=body.workerId, lease_epoch=body.leaseEpoch,
    )
    try:
        upload = repository.confirm_upload(run_id, upload_id, body.workerId, body.leaseEpoch)
    except DurableExecutionError as exc:
        _durable_error(exc)
    return {"uploadId": upload["upload_id"], "state": upload["state"]}

@router.post("/worker/result")
async def worker_result(
    body: WorkerResultIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, str]:
    """Receive the final execution result from a worker container."""
    registry = get_callback_registry()
    if settings.worker_per_task_token:
        try:
            authority = registry.get_authority(body.execution_id)
        except Exception as exc:
            logger.exception("worker result authority lookup failed execution_id=%s", body.execution_id)
            raise HTTPException(status_code=503, detail="Worker callback authority unavailable") from exc
        if authority is None:
            raise HTTPException(status_code=403, detail="Unknown or expired worker execution")
        _verify_worker_token(
            x_openair_worker_token,
            expected_task_id=authority.task_id,
            expected_owner_id=authority.owner_id,
            expected_execution_id=authority.execution_id,
            expected_execution_epoch=authority.execution_epoch,
        )
    else:
        _verify_worker_token(x_openair_worker_token)

    logger.info(
        "worker result: execution_id=%s status=%s reply_len=%d",
        body.execution_id, body.status, len(body.reply),
    )

    registry.resolve(body.execution_id, body.model_dump())

    return {"status": "ok"}


@router.post("/worker/stream-event")
async def worker_stream_event(
    body: WorkerStreamEventIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, str]:
    """Receive a streaming event from a worker container."""
    registry = get_callback_registry()
    if settings.worker_per_task_token:
        try:
            authority = registry.get_authority(body.execution_id)
        except Exception as exc:
            logger.exception("worker stream authority lookup failed execution_id=%s", body.execution_id)
            raise HTTPException(status_code=503, detail="Worker callback authority unavailable") from exc
        if authority is None:
            raise HTTPException(status_code=403, detail="Unknown or expired worker execution")
        _verify_worker_token(
            x_openair_worker_token,
            expected_task_id=authority.task_id,
            expected_owner_id=authority.owner_id,
            expected_execution_id=authority.execution_id,
            expected_execution_epoch=authority.execution_epoch,
        )
    else:
        _verify_worker_token(x_openair_worker_token)

    registry.push_event(body.execution_id, body.event)

    return {"status": "ok"}


@router.post("/worker/claim")
async def worker_claim(
    body: WorkerClaimIn,
    x_openair_worker_token: str | None = Header(None),
) -> dict[str, Any]:
    """Warm worker long-polls for its next turn.

    A keepalive worker (server mode) calls this after each turn (and repeatedly
    while idle). The handler blocks up to ``worker_poll_timeout_seconds`` for the
    API to offer a turn via ``WorkerPool.offer_turn``. Returns ``{"turn": {...}}``
    when a turn is available, or ``{"turn": null}`` on timeout (the worker then
    re-polls, or self-exits once its idle lease expires).
    """
    _verify_worker_token(x_openair_worker_token, expected_task_id=body.task_id)

    pool = get_worker_pool()
    turn = await pool.wait_for_turn(body.task_id, settings.worker_poll_timeout_seconds)
    return {"turn": turn}


@router.get("/worker/task/{task_id}")
async def worker_get_task(
    task_id: str,
    x_openair_worker_token: str | None = Header(None),
    x_openair_worker_owner: str | None = Header(None, alias="X-OpenAIR-Worker-Owner"),
) -> dict[str, Any]:
    """Worker fetches latest task state from API server.

    Trust model: with per-task tokens (Phase-2 #1) the token is bound to this task_id;
    with the legacy global token it is a shared secret, so the owner check below remains
    defense-in-depth. The caller must declare the task's owner via ``X-OpenAIR-Worker-Owner``
    and it must match the task's actual ``ownerId``; mismatches are rejected and logged.
    """
    _verify_worker_token(x_openair_worker_token, expected_task_id=task_id)

    from app.modules.task_store import store

    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    declared_owner = (x_openair_worker_owner or "").strip()
    actual_owner = str(task.get("ownerId") or "")
    if not declared_owner or declared_owner != actual_owner:
        logger.warning(
            "worker_get_task: rejected owner mismatch task=%s declared=%r actual=%r",
            task_id, declared_owner, actual_owner,
        )
        raise HTTPException(status_code=403, detail="task not bound to this worker's owner")

    return task


@router.get("/worker/containers")
async def list_worker_containers(
    x_openair_worker_token: str | None = Header(None),
) -> list[dict[str, str]]:
    """List active worker containers (for debugging/observability)."""
    _verify_worker_token(x_openair_worker_token)

    if settings.runtime_type != "container":
        return []

    from app.execution.container_manager import get_container_manager
    manager = get_container_manager()
    return manager.list_active()


# ---------------------------------------------------------------------------
# Artifact-04 — worker artifact ingest (presigned two-step + reference)
# ---------------------------------------------------------------------------

class WorkerArtifactRequestUploadIn(BaseModel):
    """通道 1 第一步：worker 申请 scoped presigned PUT。"""

    task_id: str
    capability_call_id: str | None = None
    filename: str
    size: int
    sha256: str | None = None
    kind: str = "model"
    content_type: str | None = None


class WorkerArtifactConfirmIn(BaseModel):
    """通道 1 第二步：worker 上传完后登记产物。

    ``upload_signature`` 必须是 request-upload 为该次会话签发的 HMAC——它把
    (task/owner/call/object_key/size/sha256) 绑死，禁止用有效 token 登记别的
    object_key（含跨 task 的 blob）。
    """

    task_id: str
    capability_call_id: str | None = None
    object_key: str
    storage_scope: str | None = None
    upload_signature: str | None = None  # required at runtime → missing ⇒ 401
    filename: str
    sha256: str | None = None
    size: int | None = None
    kind: str = "model"
    title: str | None = None
    summary: str | None = None
    content_type: str | None = None


class WorkerArtifactReferenceIn(BaseModel):
    """通道 2：登记外部引用（不拷贝）。"""

    task_id: str
    capability_call_id: str | None = None
    external_uri: str
    external_provider: str = "url"
    kind: str = "model"
    title: str | None = None
    summary: str | None = None


def _worker_task_or_403(task_id: str, declared_owner: str | None) -> dict[str, Any]:
    """worker-token 之上的 owner 防御层：声明 owner 必须匹配 task 实际 owner。"""
    from app.modules.task_store import store

    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    declared = (declared_owner or "").strip()
    actual = str(task.get("ownerId") or "")
    if not declared or declared != actual:
        logger.warning(
            "worker ingest: rejected owner mismatch task=%s declared=%r actual=%r",
            task_id, declared, actual,
        )
        raise HTTPException(status_code=403, detail="task not bound to this worker's owner")
    return task


@router.post("/worker/artifacts/request-upload")
async def worker_artifact_request_upload(
    body: WorkerArtifactRequestUploadIn,
    x_openair_worker_token: str | None = Header(None),
    x_openair_worker_owner: str | None = Header(None, alias="X-OpenAIR-Worker-Owner"),
) -> dict[str, Any]:
    """通道 1 第一步：签发 scoped presigned PUT URL（worker 不持 root key）。

    服务端为本次上传 mint 一个 uuid object_key（``{task}/{uuid}/{filename}``，不可变、
    全局唯一），并签发绑定该会话的 HMAC（``upload_signature``）。confirm 必须回传此
    签名，从而禁止用有效 token 登记任意 object_key（含跨 task 的 blob）。
    """
    _verify_worker_token(x_openair_worker_token, expected_task_id=body.task_id)
    task = _worker_task_or_403(body.task_id, x_openair_worker_owner)

    if settings.artifact_storage != "minio":
        raise HTTPException(status_code=503, detail="object storage not enabled")
    if body.size > settings.artifact_upload_max_bytes:
        raise HTTPException(status_code=413, detail="artifact exceeds size limit")

    from uuid import uuid4

    from app.modules.artifact_ingest import sign_upload
    from app.modules.minio_client import current_minio_storage_scope, presigned_put_object

    upload_id = uuid4().hex
    object_key = f"{body.task_id}/{upload_id}/{body.filename}"
    storage_scope = current_minio_storage_scope()
    url = presigned_put_object(
        object_key, settings.artifact_presign_ttl_seconds, storage_scope=storage_scope
    )
    upload_signature = sign_upload(
        body.task_id,
        str(task.get("ownerId") or ""),
        body.capability_call_id,
        object_key,
        body.size,
        body.sha256,
        storage_scope,
    )
    logger.info("worker ingest: presigned PUT %s (%d bytes)", object_key, body.size)
    return {
        "uploadUrl": url,
        "objectKey": object_key,
        "uploadId": upload_id,
        "storageScope": storage_scope,
        "uploadSignature": upload_signature,
    }


@router.post("/worker/artifacts/confirm")
async def worker_artifact_confirm(
    body: WorkerArtifactConfirmIn,
    x_openair_worker_token: str | None = Header(None),
    x_openair_worker_owner: str | None = Header(None, alias="X-OpenAIR-Worker-Owner"),
) -> dict[str, Any]:
    """通道 1 第二步：核对签名会话 + stat 对象 + 登记产物。

    防线（Blocker-1）：``upload_signature`` 必须是本服务对同一 (task/owner/call/
    object_key/size/sha256) 签发的 HMAC。仅凭有效 worker token+owner 不得登记任意
    object_key——杜绝把 ``other-task/secret.bin`` 这种 key 登记成本 task 产物。
    """
    _verify_worker_token(x_openair_worker_token, expected_task_id=body.task_id)
    task = _worker_task_or_403(body.task_id, x_openair_worker_owner)
    owner_id = str(task.get("ownerId") or "")

    from app.modules.artifact_ingest import verify_upload
    from app.modules.minio_client import stat_object

    # object_key 必须 task-scoped（额外防御：即便签名校验通过也不应跨 task）
    if not body.object_key.startswith(f"{body.task_id}/"):
        raise HTTPException(status_code=401, detail="object key not bound to this task")
    if not str(body.storage_scope or "").strip():
        raise HTTPException(status_code=401, detail="storage scope missing from upload session")
    # 会话签名校验：把 task/owner/call/storage_scope/object_key/size/sha256 绑死
    if not verify_upload(
        body.task_id,
        owner_id,
        body.capability_call_id,
        body.object_key,
        body.size,
        body.sha256,
        body.storage_scope,
        body.upload_signature,
    ):
        raise HTTPException(status_code=401, detail="invalid or unbound upload signature")

    # 重放去重：同一 object_key 不得重复登记（一个 blob → 一条产物）
    if any((a.get("externalKey") == body.object_key) for a in task.get("artifacts") or []):
        raise HTTPException(status_code=409, detail="object already registered as an artifact")

    stat = stat_object(body.object_key, storage_scope=body.storage_scope)
    if stat is None:
        raise HTTPException(status_code=404, detail="uploaded object not found")
    size = body.size if body.size is not None else stat.get("size")
    if body.size is not None and stat.get("size") is not None and body.size != stat["size"]:
        raise HTTPException(status_code=400, detail="size mismatch")

    from app.modules.task_store import store

    extra: dict[str, Any] = {
        "origin": "remote_compute",
        "storageType": "external",
        "storageScope": body.storage_scope,
        "externalKey": body.object_key,
        "contentSize": size,
        "format": (body.filename.rsplit(".", 1)[-1].lower() if "." in body.filename else None),
    }
    if body.sha256:
        extra["contentSha256"] = body.sha256
    artifact = store.add_artifact(
        body.task_id,
        kind=body.kind,
        title=body.title or body.filename,
        summary=body.summary,
        storage_type="external",
        source_capability_call_id=body.capability_call_id,
        extra_fields=extra,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="task not found")
    logger.info("worker ingest: confirmed %s → artifact %s", body.object_key, artifact.get("id"))
    return artifact


@router.post("/worker/artifacts/reference")
async def worker_artifact_reference(
    body: WorkerArtifactReferenceIn,
    x_openair_worker_token: str | None = Header(None),
    x_openair_worker_owner: str | None = Header(None, alias="X-OpenAIR-Worker-Owner"),
) -> dict[str, Any]:
    """通道 2：登记外部引用（storage_type=external + external_uri，不拷贝）。"""
    _verify_worker_token(x_openair_worker_token, expected_task_id=body.task_id)
    _worker_task_or_403(body.task_id, x_openair_worker_owner)

    from app.modules.task_store import store

    extra = {
        "origin": "remote_compute",
        "storageType": "external",
        "externalProvider": body.external_provider,
        "externalUri": body.external_uri,
    }
    artifact = store.add_artifact(
        body.task_id,
        kind=body.kind,
        title=body.title or body.external_uri,
        summary=body.summary,
        source_capability_call_id=body.capability_call_id,
        extra_fields=extra,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="task not found")
    return artifact

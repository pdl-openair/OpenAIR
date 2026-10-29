"""Preserve tool responses using existing checkpoint and artifact storage."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.config import settings
from app.execution.research_internal_skill_call import (
    InternalSkillCallError,
    PreparedInternalSkillCall,
)
from app.models.research_orchestration import ResultV1
from app.modules.artifact_write import ArtifactStorageError
from app.modules.external_execution_checkpoint import ExternalExecutionCheckpointError


def _wire(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _storage_identity(call_id: str, task_id: str) -> tuple[str, str | None, str]:
    reference = f"internal-calls/{sha256(call_id.encode('utf-8')).hexdigest()}.json"
    if settings.artifact_storage == "local":
        return "local", task_id, reference
    if settings.artifact_storage == "minio":
        from app.modules.minio_client import current_minio_storage_scope

        return "external", current_minio_storage_scope(), reference
    raise ArtifactStorageError("artifact_storage_backend_invalid")


def _read_completed_envelope(checkpoint: Any, dispatch_id: str) -> Mapping[str, Any]:
    if (
        checkpoint is None
        or checkpoint.dispatch_id != dispatch_id
        or checkpoint.phase not in {"response_received", "canonical_completed"}
        or checkpoint.response_kind != "dispatch_completed"
        or checkpoint.response_payload is None
    ):
        raise InternalSkillCallError("internal_skill_response_payload_missing")
    if sha256(checkpoint.response_payload).hexdigest() != checkpoint.response_hash:
        raise InternalSkillCallError("internal_skill_response_receipt_hash_mismatch")
    return json.loads(checkpoint.response_payload)


def _stage_completed_response(checkpoints: Any, call_id: str, dispatch_id: str, envelope: Any):
    try:
        checkpoint = checkpoints.stage_response(
            capability_call_id=call_id, response_kind="dispatch_completed", payload=_wire(envelope)
        )
    except ExternalExecutionCheckpointError as exc:
        if str(exc) != "external_checkpoint_response_conflict":
            raise
        # A competing processor may have committed or completed its receipt.
        # Adopt that receipt only; this path never invokes the physical action.
        checkpoint = checkpoints.get(capability_call_id=call_id)
        if (
            checkpoint is None
            or checkpoint.dispatch_id != dispatch_id
            or checkpoint.response_kind != "dispatch_completed"
            or checkpoint.phase not in {"response_received", "canonical_completed"}
        ):
            raise
    return checkpoint, _read_completed_envelope(checkpoint, dispatch_id)


def preserve_internal_runtime_response(
    *,
    checkpoints: Any,
    storage: Any,
    prepared: PreparedInternalSkillCall,
    dispatch_id: str,
    response: Mapping[str, Any] | None,
    known_not_submitted: bool = False,
) -> tuple[ResultV1, Mapping[str, Any], str]:
    """Freeze storage identity before byte I/O; replay never chooses new defaults.

    A valid tool response is evidence even when state is failed or unknown.
    The returned Result passes only response/storage integrity, not science.
    """
    call = prepared.call
    task_id = str(prepared.request["taskId"])
    checkpoint = checkpoints.get(capability_call_id=call.capability_call_id)
    if checkpoint is None or checkpoint.dispatch_id != dispatch_id:
        raise InternalSkillCallError("internal_skill_response_checkpoint_missing")
    if checkpoint.response_kind == "dispatch_completed":
        envelope = _read_completed_envelope(checkpoint, dispatch_id)
    else:
        if not isinstance(response, Mapping) or response.get("state") not in {
            "succeeded",
            "failed",
            "unknown",
        }:
            raise InternalSkillCallError("internal_skill_response_state_invalid")
        if checkpoint.phase not in {"submitted", "response_received"} and not (
            checkpoint.phase == "prepared"
            and known_not_submitted
            and call.status.value in {"queued", "running"}
            and response.get("state") == "failed"
        ):
            raise InternalSkillCallError("internal_skill_response_without_submission")
        storage_type, storage_scope, storage_ref = _storage_identity(
            call.capability_call_id, task_id
        )
        body = {
            "schemaVersion": 2,
            "title": prepared.request["skillId"],
            "mimeType": "application/json",
            "content": _wire(response).decode("utf-8"),
            "providerId": "direct_skill_runtime",
            "adapter": prepared.request["skillId"],
            "resultProtocol": response.get("resultProtocol", {}),
            "warnings": response.get("warnings", []),
        }
        physical = (
            checkpoint.response_payload.decode("utf-8")
            if checkpoint.response_payload is not None
            else None
        )
        envelope = {
            "internalCallResultVersion": 1,
            "capabilityCallId": call.capability_call_id,
            "parentCapabilityCallId": prepared.parent_call_id,
            "body": body,
            "physicalResponse": physical,
            "physicalResponseHash": checkpoint.response_hash,
            "runtimeState": response["state"],
            "knownNotSubmitted": known_not_submitted,
            "resultId": f"result:internal:{sha256(call.capability_call_id.encode()).hexdigest()}",
            "storageType": storage_type,
            "storageScope": storage_scope,
            "storageRef": storage_ref,
            "contentSha256": sha256(_wire(body)).hexdigest(),
            "createdAt": datetime.now(UTC).isoformat(),
        }
        # Response and immutable storage reservation become durable first.
        checkpoint, envelope = _stage_completed_response(
            checkpoints, call.capability_call_id, dispatch_id, envelope
        )
    if (
        not isinstance(envelope, Mapping)
        or envelope.get("internalCallResultVersion") != 1
        or envelope.get("capabilityCallId") != call.capability_call_id
        or envelope.get("parentCapabilityCallId") != prepared.parent_call_id
    ):
        raise InternalSkillCallError("internal_skill_response_identity_conflict")
    blob = _wire(envelope["body"])
    if sha256(blob).hexdigest() != envelope["contentSha256"]:
        raise InternalSkillCallError("internal_skill_response_body_hash_mismatch")
    # Resume the exact frozen storage target, never silently switch its backend.
    if (envelope["storageType"] == "external") != (settings.artifact_storage == "minio"):
        raise ArtifactStorageError("internal_skill_response_storage_backend_changed")
    stored = storage.store_durable_bytes(
        task_id,
        envelope["storageRef"],
        blob,
        "application/json",
        storage_scope=envelope["storageScope"],
    )
    if (stored.storage_type, stored.storage_scope, stored.storage_ref) != (
        envelope["storageType"],
        envelope["storageScope"],
        envelope["storageRef"],
    ):
        raise ArtifactStorageError("internal_skill_response_storage_identity_conflict")
    readback = storage.read_durable_bytes(
        task_id, stored.storage_type, stored.storage_ref, stored.storage_scope
    )
    if readback is None or sha256(readback).hexdigest() != envelope["contentSha256"]:
        raise ArtifactStorageError("internal_skill_response_storage_hash_mismatch")
    result = ResultV1(
        resultId=envelope["resultId"],
        workUnitId=call.work_unit_id,
        capabilityCallId=call.capability_call_id,
        storageType=stored.storage_type,
        storageScope=stored.storage_scope,
        storageRef=stored.storage_ref,
        contentSha256=envelope["contentSha256"],
        mimeType="application/json",
        checkStatus="passed",
        checkResult={
            "status": "passed",
            "checkScope": "tool_response_and_storage_integrity",
            "executionStatus": envelope["runtimeState"],
        },
        sourceRefs=[prepared.parent_call_id],
        artifactRefs=[],
        scientificStateVersions=[],
        createdAt=envelope["createdAt"],
    )
    return result, json.loads(envelope["body"]["content"]), checkpoint.response_hash

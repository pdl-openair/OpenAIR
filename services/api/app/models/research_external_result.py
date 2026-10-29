"""Strict internal contracts for external execution recovery facts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from pydantic.alias_generators import to_camel


NonEmptyStr = Annotated[str, Field(min_length=1, strict=True)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]


class StrictInternalModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        strict=True,
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class ExternalExecutionState(StrEnum):
    NEVER_SUBMITTED = "never_submitted"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    KNOWN_FAILED = "known_failed"
    UNKNOWN = "unknown"


class EvidenceKind(StrEnum):
    REJECTED_RESPONSE = "rejected_response"
    RESULT_RESPONSE = "result_response"
    PROVIDER_QUERY = "provider_query"
    TRANSPORT_FAILURE = "transport_failure"
    RECONCILIATION = "reconciliation"


class FailureClass(StrEnum):
    RESPONSE_CONTRACT = "response_contract"
    FINISH_REASON = "finish_reason"
    PROVIDER_KNOWN_REJECTION = "provider_known_rejection"
    TRANSPORT_UNKNOWN = "transport_unknown"
    VALIDATION = "validation"


class EvidenceLineage(StrictInternalModel):
    task_id: NonEmptyStr
    execution_decision_id: NonEmptyStr
    work_unit_id: NonEmptyStr
    capability_call_id: NonEmptyStr
    dispatch_id: NonEmptyStr
    attempt_number: PositiveInt


class SafeValidatorDiagnostic(StrictInternalModel):
    error_code: NonEmptyStr
    category: NonEmptyStr
    json_pointer: str = ""
    field_path: str = ""
    expected_type: str | None = None
    actual_type: str | None = None
    validator_keyword: NonEmptyStr

    @model_validator(mode="after")
    def bounded_safe_fields(self) -> "SafeValidatorDiagnostic":
        for value in (
            self.error_code,
            self.category,
            self.json_pointer,
            self.field_path,
            self.expected_type or "",
            self.actual_type or "",
            self.validator_keyword,
        ):
            if len(value) > 160 or any(ord(char) < 32 for char in value):
                raise ValueError("diagnostic fields must be bounded stable identifiers")
        return self


class RejectedResponseDiagnosis(StrictInternalModel):
    category: NonEmptyStr
    normalized_finish_reason: NonEmptyStr
    diagnostics: tuple[SafeValidatorDiagnostic, ...]


class EvidenceCaptureRequest(StrictInternalModel):
    evidence_id: NonEmptyStr
    lineage: EvidenceLineage
    owner_id: NonEmptyStr
    workspace_id: NonEmptyStr
    provider_id: NonEmptyStr
    adapter: NonEmptyStr
    evidence_kind: EvidenceKind
    failure_class: FailureClass
    normalized_finish_reason: NonEmptyStr
    diagnostics: tuple[SafeValidatorDiagnostic, ...]
    canonical_metadata_fingerprint: Sha256Hex
    canonical_payload_fingerprint: Sha256Hex
    reconciliation_evidence_refs: tuple[NonEmptyStr, ...]


class RecordedEvidenceEnvelope(StrictInternalModel):
    evidence_id: NonEmptyStr
    lineage: EvidenceLineage
    owner_id: NonEmptyStr
    workspace_id: NonEmptyStr
    provider_id: NonEmptyStr
    adapter: NonEmptyStr
    evidence_kind: EvidenceKind
    failure_class: FailureClass
    normalized_finish_reason: NonEmptyStr
    diagnostics: tuple[SafeValidatorDiagnostic, ...]
    content_sha256: Sha256Hex
    byte_length: NonNegativeInt
    truncated: StrictBool
    hash_incomplete: StrictBool
    created_at: datetime
    expires_at: datetime
    purged_at: datetime | None = None
    raw_body: bytes | None = Field(default=None, repr=False)
    canonical_metadata_fingerprint: Sha256Hex
    canonical_payload_fingerprint: Sha256Hex
    reconciliation_evidence_refs: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def validate_raw_storage_shape(self) -> "RecordedEvidenceEnvelope":
        if (self.raw_body is None) != (self.purged_at is not None):
            raise ValueError("raw evidence is present until it is purged")
        return self


class ReconcileResolution(StrEnum):
    QUERY_PROVIDER = "query_provider"


class ReconcileExternalResultCommand(StrictInternalModel):
    task_id: NonEmptyStr
    work_unit_id: NonEmptyStr
    dispatch_id: NonEmptyStr
    execution_decision_id: NonEmptyStr
    capability_call_id: NonEmptyStr
    attempt_number: PositiveInt
    resolution: ReconcileResolution
    evidence_refs: tuple[NonEmptyStr, ...]
    command_request_id: NonEmptyStr
    canonical_payload_fingerprint: Sha256Hex
    expected_task_revision: NonNegativeInt
    owner_id: NonEmptyStr
    workspace_id: NonEmptyStr
    actor_id: NonEmptyStr

    @model_validator(mode="after")
    def require_evidence(self) -> "ReconcileExternalResultCommand":
        if not self.evidence_refs:
            raise ValueError("reconciliation requires protected evidence refs")
        return self


class ExplicitRetryCommand(StrictInternalModel):
    task_id: NonEmptyStr
    work_unit_id: NonEmptyStr
    dispatch_id: NonEmptyStr
    execution_decision_id: NonEmptyStr
    prior_capability_call_id: NonEmptyStr
    prior_attempt_number: PositiveInt
    command_request_id: NonEmptyStr
    canonical_payload_fingerprint: Sha256Hex
    expected_task_revision: NonNegativeInt
    owner_id: NonEmptyStr
    workspace_id: NonEmptyStr
    actor_id: NonEmptyStr
    reason_code: NonEmptyStr


class FrozenExternalQuery(StrictInternalModel):
    task_id: NonEmptyStr
    work_unit_id: NonEmptyStr
    dispatch_id: NonEmptyStr
    execution_decision_id: NonEmptyStr
    capability_call_id: NonEmptyStr
    attempt_number: PositiveInt
    expected_task_revision: NonNegativeInt
    authorization_task_revision: NonNegativeInt | None
    owner_id: NonEmptyStr
    workspace_id: NonEmptyStr
    provider_id: NonEmptyStr
    adapter: NonEmptyStr
    provider_idempotency_key: NonEmptyStr
    remote_run_id: NonEmptyStr | None
    external_state: ExternalExecutionState
    frozen_work_unit_fingerprint: Sha256Hex
    proven_no_external_action_retry: StrictBool = False


class ExternalQueryObservation(StrictInternalModel):
    external_state: ExternalExecutionState
    remote_run_id: NonEmptyStr | None = None
    error_code: NonEmptyStr | None = None
    result_evidence_refs: tuple[NonEmptyStr, ...] = ()


class RecoveryDirective(StrictInternalModel):
    directive_id: NonEmptyStr
    task_id: NonEmptyStr
    source_execution_decision_id: NonEmptyStr
    source_work_unit_id: NonEmptyStr
    resolution_evidence_ref: NonEmptyStr
    new_selection_request_id: NonEmptyStr
    expected_task_revision: NonNegativeInt
    canonical_payload_fingerprint: Sha256Hex


__all__ = [
    "EvidenceCaptureRequest",
    "EvidenceKind",
    "EvidenceLineage",
    "ExplicitRetryCommand",
    "ExternalExecutionState",
    "ExternalQueryObservation",
    "FailureClass",
    "FrozenExternalQuery",
    "RecordedEvidenceEnvelope",
    "ReconcileExternalResultCommand",
    "ReconcileResolution",
    "RecoveryDirective",
    "RejectedResponseDiagnosis",
    "SafeValidatorDiagnostic",
]

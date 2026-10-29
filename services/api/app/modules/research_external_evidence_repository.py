"""Raw external evidence storage boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, Mapping

from app.models.research_external_result import (
    EvidenceCaptureRequest,
    RecordedEvidenceEnvelope,
)


DEFAULT_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 30
DEFAULT_CAPTURE_BYTES = 64 * 1024


class EvidenceUnavailableError(RuntimeError):
    pass


class EvidenceIntegrityError(RuntimeError):
    pass


class ExternalEvidenceRecorder:
    def __init__(
        self,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_capture_bytes: int = DEFAULT_CAPTURE_BYTES,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if type(retention_days) is not int or not 1 <= retention_days <= MAX_RETENTION_DAYS:
            raise ValueError("external_evidence_retention_days_invalid")
        if type(max_capture_bytes) is not int or max_capture_bytes < 1:
            raise ValueError("external_evidence_capture_limit_invalid")
        self._retention_days = retention_days
        self._max_capture_bytes = max_capture_bytes
        self._now = now or (lambda: datetime.now(timezone.utc))

    def record(
        self,
        request: EvidenceCaptureRequest,
        raw_body: bytes,
        *,
        total_byte_length: int | None = None,
        full_content_sha256: str | None = None,
    ) -> RecordedEvidenceEnvelope:
        if not isinstance(raw_body, bytes):
            raise TypeError("raw evidence must be bytes")
        total = len(raw_body) if total_byte_length is None else total_byte_length
        if type(total) is not int or total < len(raw_body) or total < 0:
            raise ValueError("invalid total evidence byte length")
        captured = raw_body[: self._max_capture_bytes]
        truncated = total > len(captured)
        if full_content_sha256 is not None:
            if len(full_content_sha256) != 64 or any(char not in "0123456789abcdef" for char in full_content_sha256):
                raise ValueError("invalid full content sha256")
            digest = full_content_sha256
            hash_incomplete = False
        else:
            digest = hashlib.sha256(captured).hexdigest()
            hash_incomplete = truncated
        created_at = self._now()
        common = dict(
            **request.model_dump(),
            content_sha256=digest,
            byte_length=total,
            truncated=truncated,
            hash_incomplete=hash_incomplete,
            created_at=created_at,
            expires_at=created_at + timedelta(days=self._retention_days),
        )
        return RecordedEvidenceEnvelope(
            **common,
            raw_body=captured,
        )

    def read(
        self,
        envelope: RecordedEvidenceEnvelope,
        *,
        owner_id: str,
        workspace_id: str,
        now: datetime | None = None,
    ) -> bytes:
        if envelope.owner_id != owner_id or envelope.workspace_id != workspace_id:
            raise EvidenceUnavailableError("evidence_scope_mismatch")
        observed_at = now or self._now()
        if observed_at >= envelope.expires_at:
            raise EvidenceUnavailableError("evidence_expired")
        if envelope.raw_body is None:
            raise EvidenceUnavailableError("raw_evidence_unavailable")
        if not envelope.truncated:
            if len(envelope.raw_body) != envelope.byte_length:
                raise EvidenceIntegrityError("evidence_content_length_mismatch")
            if hashlib.sha256(envelope.raw_body).hexdigest() != envelope.content_sha256:
                raise EvidenceIntegrityError("evidence_content_hash_mismatch")
        elif envelope.hash_incomplete and hashlib.sha256(envelope.raw_body).hexdigest() != envelope.content_sha256:
            raise EvidenceIntegrityError("evidence_content_hash_mismatch")
        return envelope.raw_body


class ResearchExternalEvidenceRepository:
    """PostgreSQL raw-store boundary; reads remain scoped and audited."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], ContextManager[Any]],
        recorder: ExternalEvidenceRecorder,
    ) -> None:
        self._connection_factory = connection_factory
        self._recorder = recorder

    def put(self, envelope: RecordedEvidenceEnvelope) -> None:
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                self.insert_with_cursor(cursor, envelope)

    @staticmethod
    def insert_with_cursor(cursor: Any, envelope: RecordedEvidenceEnvelope) -> None:
        lineage = envelope.lineage
        cursor.execute(
            """INSERT INTO openair_external_evidence (
                   evidence_id, task_id, execution_decision_id, work_unit_id,
                   capability_call_id, dispatch_id, attempt_number, owner_id,
                   workspace_id, provider_id, adapter, evidence_kind, failure_class,
                   normalized_finish_reason, diagnostics, content_sha256, byte_length,
                   truncated, hash_incomplete, raw_body,
                   canonical_metadata_fingerprint, canonical_payload_fingerprint,
                   reconciliation_evidence_refs, created_at, expires_at, purged_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s)""",
            (
                envelope.evidence_id,
                lineage.task_id,
                lineage.execution_decision_id,
                lineage.work_unit_id,
                lineage.capability_call_id,
                lineage.dispatch_id,
                lineage.attempt_number,
                envelope.owner_id,
                envelope.workspace_id,
                envelope.provider_id,
                envelope.adapter,
                envelope.evidence_kind.value,
                envelope.failure_class.value,
                envelope.normalized_finish_reason,
                json.dumps([item.model_dump(mode="json", by_alias=True) for item in envelope.diagnostics]),
                envelope.content_sha256,
                envelope.byte_length,
                envelope.truncated,
                envelope.hash_incomplete,
                envelope.raw_body,
                envelope.canonical_metadata_fingerprint,
                envelope.canonical_payload_fingerprint,
                list(envelope.reconciliation_evidence_refs),
                envelope.created_at,
                envelope.expires_at,
                envelope.purged_at,
            ),
        )

    def read_raw(
        self,
        *,
        evidence_id: str,
        owner_id: str,
        workspace_id: str,
        actor_id: str,
        purpose_code: str,
        access_id: str,
        now: datetime | None = None,
    ) -> bytes:
        if not all(str(value or "").strip() for value in (actor_id, purpose_code, access_id)):
            raise ValueError("audited evidence access identity is required")
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT * FROM openair_external_evidence
                        WHERE evidence_id = %s AND owner_id = %s AND workspace_id = %s
                        FOR UPDATE""",
                    (evidence_id, owner_id, workspace_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise EvidenceUnavailableError("evidence_not_found")
                envelope = _envelope_from_row(row)
                raw_body = self._recorder.read(
                    envelope,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    now=now,
                )
                cursor.execute(
                    """INSERT INTO openair_external_evidence_access_audit (
                           access_id, evidence_id, owner_id, workspace_id, actor_id, purpose_code)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (access_id, evidence_id, owner_id, workspace_id, actor_id, purpose_code),
                )
                return raw_body

    def purge_expired(self, *, now: datetime | None = None) -> int:
        observed_at = now or datetime.now(timezone.utc)
        with self._connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE openair_external_evidence
                          SET raw_body = NULL, purged_at = %s
                        WHERE raw_body IS NOT NULL AND expires_at <= %s""",
                    (observed_at, observed_at),
                )
                return cursor.rowcount


def _envelope_from_row(row: Mapping[str, Any]) -> RecordedEvidenceEnvelope:
    def binary(name: str) -> bytes | None:
        value = row.get(name)
        return None if value is None else bytes(value)

    return RecordedEvidenceEnvelope.model_validate(
        {
            "evidenceId": row["evidence_id"],
            "lineage": {
                "taskId": row["task_id"],
                "executionDecisionId": row["execution_decision_id"],
                "workUnitId": row["work_unit_id"],
                "capabilityCallId": row["capability_call_id"],
                "dispatchId": row["dispatch_id"],
                "attemptNumber": int(row["attempt_number"]),
            },
            "ownerId": row["owner_id"],
            "workspaceId": row["workspace_id"],
            "providerId": row["provider_id"],
            "adapter": row["adapter"],
            "evidenceKind": row["evidence_kind"],
            "failureClass": row["failure_class"],
            "normalizedFinishReason": row["normalized_finish_reason"],
            "diagnostics": row["diagnostics"],
            "contentSha256": row["content_sha256"],
            "byteLength": int(row["byte_length"]),
            "truncated": row["truncated"],
            "hashIncomplete": row["hash_incomplete"],
            "createdAt": row["created_at"],
            "expiresAt": row["expires_at"],
            "purgedAt": row["purged_at"],
            "rawBody": binary("raw_body"),
            "canonicalMetadataFingerprint": row["canonical_metadata_fingerprint"],
            "canonicalPayloadFingerprint": row["canonical_payload_fingerprint"],
            "reconciliationEvidenceRefs": tuple(row["reconciliation_evidence_refs"]),
        }
    )


__all__ = [
    "DEFAULT_CAPTURE_BYTES",
    "DEFAULT_RETENTION_DAYS",
    "EvidenceIntegrityError",
    "EvidenceUnavailableError",
    "ExternalEvidenceRecorder",
    "ResearchExternalEvidenceRepository",
    "MAX_RETENTION_DAYS",
]

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.research_external_result import (
    EvidenceCaptureRequest,
    EvidenceKind,
    EvidenceLineage,
    FailureClass,
)
from app.modules.research_external_evidence_repository import (
    EvidenceIntegrityError,
    EvidenceUnavailableError,
    ExternalEvidenceRecorder,
)
from app.execution.openai_responses_provider import diagnose_rejected_response


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
RAW_SECRET = b'{"token":"fictional-sensitive-canary","value":7}'
LINEAGE = EvidenceLineage(
    task_id="task-1",
    execution_decision_id="decision-1",
    work_unit_id="work-unit-1",
    capability_call_id="call-1",
    dispatch_id="dispatch-1",
    attempt_number=1,
)


def _request() -> EvidenceCaptureRequest:
    return EvidenceCaptureRequest(
        evidence_id="evidence-1",
        lineage=LINEAGE,
        owner_id="owner-1",
        workspace_id="workspace-1",
        provider_id="fake-provider",
        adapter="fake-adapter",
        evidence_kind=EvidenceKind.REJECTED_RESPONSE,
        failure_class=FailureClass.PROVIDER_KNOWN_REJECTION,
        normalized_finish_reason="content_filter",
        diagnostics=(),
        canonical_metadata_fingerprint="a" * 64,
        canonical_payload_fingerprint="b" * 64,
        reconciliation_evidence_refs=(),
    )


def test_raw_response_is_recorded_without_keyring_configuration() -> None:
    recorder = ExternalEvidenceRecorder(retention_days=7, now=lambda: NOW)

    envelope = recorder.record(_request(), RAW_SECRET)

    assert envelope.raw_body == RAW_SECRET
    assert envelope.content_sha256
    assert envelope.byte_length == len(RAW_SECRET)
    assert recorder.read(envelope, owner_id="owner-1", workspace_id="workspace-1", now=NOW) == RAW_SECRET


def test_raw_response_hash_scope_and_expiry_are_checked() -> None:
    recorder = ExternalEvidenceRecorder(retention_days=7, now=lambda: NOW)
    envelope = recorder.record(_request(), RAW_SECRET)
    tampered = envelope.model_copy(update={"raw_body": b"tampered"})
    with pytest.raises(EvidenceIntegrityError):
        recorder.read(tampered, owner_id="owner-1", workspace_id="workspace-1", now=NOW)

    with pytest.raises(EvidenceUnavailableError, match="evidence_expired"):
        recorder.read(
            envelope,
            owner_id="owner-1",
            workspace_id="workspace-1",
            now=NOW + timedelta(days=8),
        )
    with pytest.raises(EvidenceUnavailableError, match="evidence_scope_mismatch"):
        recorder.read(envelope, owner_id="wrong", workspace_id="workspace-1", now=NOW)


@pytest.mark.parametrize(
    ("raw", "finish_reason", "category", "pointer", "keyword"),
    (
        (b"plain prose", "stop", "prose", "", "json_parse"),
        (b'{"title":"cut', "stop", "truncated_json", "", "json_parse"),
        (b'{"title":"x","mimeType":"text/plain","content":"ok","extra":1}', "stop", "extra_fields", "/extra", "additionalProperties"),
        (b"title: [broken", "stop", "invalid_yaml", "", "yaml_parse"),
        (b'{"title":7,"mimeType":"text/plain","content":"ok"}', "stop", "schema_type", "/title", "type"),
        (b'{"title":"x","mimeType":"application/xml","content":"ok"}', "stop", "schema_enum", "/mimeType", "enum"),
        (b'{"title":"x"}', "stop", "schema_required", "/mimeType", "required"),
        (b'{"title":"x","mimeType":"text/plain","content":"ok"}', "length", "finish_reason_non_success", "/finish_reason", "const"),
    ),
)
def test_rejected_response_diagnostics_are_precise_bounded_and_value_free(
    raw: bytes, finish_reason: str, category: str, pointer: str, keyword: str
) -> None:
    result = diagnose_rejected_response(raw, finish_reason=finish_reason)

    assert result.category == category
    assert result.diagnostics[0].json_pointer == pointer
    assert result.diagnostics[0].validator_keyword == keyword
    serialized = result.model_dump_json()
    assert "plain prose" not in serialized
    assert "application/xml" not in serialized
    assert '"extra":1' not in serialized


def test_capture_limit_marks_truncation_without_unbounded_plaintext_retention() -> None:
    raw = b"x" * 100
    recorder = ExternalEvidenceRecorder(max_capture_bytes=16, now=lambda: NOW)
    envelope = recorder.record(
        _request(), raw, total_byte_length=100, full_content_sha256=None
    )

    assert envelope.truncated is True
    assert envelope.hash_incomplete is True
    assert envelope.byte_length == 100
    assert envelope.raw_body == b"x" * 16
    assert recorder.read(envelope, owner_id="owner-1", workspace_id="workspace-1", now=NOW) == b"x" * 16


def test_recorder_rejects_invalid_retention_and_capture_limits() -> None:
    with pytest.raises(ValueError, match="retention_days_invalid"):
        ExternalEvidenceRecorder(retention_days=31)
    with pytest.raises(ValueError, match="capture_limit_invalid"):
        ExternalEvidenceRecorder(max_capture_bytes=0)

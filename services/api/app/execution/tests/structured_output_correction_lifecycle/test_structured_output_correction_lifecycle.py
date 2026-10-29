from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.execution import durable_skill_runtime, skill_finalization_runtime
from app.modules.provider_transport_evidence import ProviderHttpAttempt


def _canonical_schema_sha256(schema: dict) -> str:
    encoded = json.dumps(
        schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _generic_frozen_finalizer_skill() -> dict:
    input_schema = {
        "type": "object",
        "required": ["source"],
        "properties": {"source": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    draft_schema = {
        "type": "object",
        "required": ["draft"],
        "properties": {"draft": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["canonical"],
        "properties": {"canonical": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    return {
        "skillId": "generic_frozen_finalizer",
        "displayName": "Generic Frozen Finalizer",
        "skillPath": "skills/test/generic-frozen-finalizer",
        "sourceContract": {
            "frozenSchemas": {
                "input": {"sha256": _canonical_schema_sha256(input_schema), "schema": input_schema},
                "output": {"sha256": _canonical_schema_sha256(output_schema), "schema": output_schema},
            },
            "frozenFinalization": {
                "contractVersion": 1,
                "entrypoint": {"module": "scripts.finalize", "callable": "finalize"},
                "codeSha256": "a" * 64,
                "draftSchema": {
                    "path": "draft.schema.json",
                    "sha256": _canonical_schema_sha256(draft_schema),
                    "schema": draft_schema,
                },
            },
        },
    }


def _frozen_execution_policy() -> dict:
    return {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "frozen-policy-model",
        "modelProfile": "terra",
        "reasoningEffort": "none",
        "maxOutputTokens": 6000,
        "readTimeoutSeconds": 60.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": 1,
    }


def _execute(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response_contents: list[object],
    status_codes: list[int] | None = None,
    finalize=None,
) -> tuple[dict, list[dict], list[dict]]:
    skill = _generic_frozen_finalizer_skill()
    runtime = {
        **skill,
        "runtimeKind": "llm_reasoning",
        "adapterId": "huiyan_chat_completions",
        "modelProfile": None,
    }
    requests: list[dict] = []
    evidence: list[dict] = []
    contents = list(response_contents)
    statuses = list(status_codes or [200] * len(contents))

    class EvidenceRepository:
        def begin(self, **kwargs) -> ProviderHttpAttempt:
            attempt = ProviderHttpAttempt(
                attempt_id="attempt-primary",
                capability_call_id=str(kwargs["capability_call_id"]),
                attempt_number=1,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
            )
            evidence.append({"kind": "primary", "attemptId": attempt.attempt_id, **kwargs})
            return attempt

        def begin_sub_attempt(self, **kwargs) -> ProviderHttpAttempt:
            attempt = ProviderHttpAttempt(
                attempt_id="attempt-recovery",
                capability_call_id=str(kwargs["capability_call_id"]),
                attempt_number=2,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
                evidence_kind=str(kwargs["evidence_kind"]),
                adapter_name=str(kwargs["adapter_name"]),
            )
            evidence.append({"kind": "recovery", "attemptId": attempt.attempt_id, **kwargs})
            return attempt

        def mark_io_started(self, attempt: ProviderHttpAttempt) -> None:
            matching = [row for row in evidence if row["attemptId"] == attempt.attempt_id]
            assert len(matching) == 1
            matching[0]["ioStarted"] = True

        def finish(self, attempt: ProviderHttpAttempt, **kwargs) -> None:
            matching = [row for row in evidence if row["attemptId"] == attempt.attempt_id]
            assert len(matching) == 1
            matching[0]["outcome"] = kwargs["outcome"]

    class Response:
        headers: dict[str, str] = {}

        def __init__(self, content: str, status_code: int) -> None:
            self.status_code = status_code
            self.content = json.dumps({
                "choices": [{"message": {"content": content}}],
                "model": "frozen-policy-model",
            }).encode("utf-8")

        def raise_for_status(self) -> None:
            if self.status_code < 400:
                return
            request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, content=self.content)
            raise httpx.HTTPStatusError(
                "provider rejected request", request=request, response=response
            )

    class Client:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, content: bytes, **_kwargs) -> Response:
            requests.append(json.loads(content))
            if not contents:
                raise AssertionError("unexpected third Provider request")
            item = contents.pop(0)
            status = statuses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return Response(str(item), status)

    monkeypatch.setattr(
        durable_skill_runtime,
        "apply_provider_schema_transport",
        lambda _provider, *, payload, **_kwargs: dict(payload),
    )
    monkeypatch.setattr(
        durable_skill_runtime, "resolve_registered_durable_skill", lambda _skill_id: skill
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_read_skill_contract",
        lambda _skill: ({"output_file": "result.json"}, "Execute."),
    )
    if finalize is None:
        finalize = lambda **_kwargs: skill_finalization_runtime.FinalizedSkillOutput(
            output={"canonical": "final output"}, status="succeeded"
        )
    monkeypatch.setattr(
        skill_finalization_runtime, "finalize_and_validate_registered_output", finalize
    )
    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", EvidenceRepository)
    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=Client,
            HTTPStatusError=httpx.HTTPStatusError,
            HTTPError=httpx.HTTPError,
        ),
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.delenv("LLM_PROVIDER_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_MODEL_TERRA", raising=False)

    result = durable_skill_runtime.execute_production_durable_skill({
        "skillId": skill["skillId"],
        "parameters": {"source": "frozen input"},
        "frozenGoal": "Finalize one generic draft.",
        "resolvedExecutionPolicy": _frozen_execution_policy(),
        "providerEvidence": {
            "capabilityCallId": "call-frozen-policy",
            "attemptNumber": 1,
        },
    })
    return result, requests, evidence


def test_parse_failure_uses_exactly_one_correction_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence = _execute(
        monkeypatch,
        response_contents=["not-json", json.dumps({"draft": "corrected draft"})],
    )

    assert result["state"] == "succeeded"
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]
    assert [row["attemptId"] for row in evidence] == ["attempt-primary", "attempt-recovery"]
    assert {row["capability_call_id"] for row in evidence} == {"call-frozen-policy"}


def test_finalizer_rejection_uses_exactly_one_correction_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drafts: list[dict] = []

    def finalize(**kwargs):
        drafts.append(dict(kwargs["draft_payload"]))
        if len(drafts) == 1:
            raise skill_finalization_runtime.SkillFinalizationError(
                "finalizer_rejected", errors=("retrieval_strategy_intent_duplicate",)
            )
        return skill_finalization_runtime.FinalizedSkillOutput(
            output={"canonical": "corrected final output"}, status="succeeded"
        )

    result, requests, evidence = _execute(
        monkeypatch,
        response_contents=[
            json.dumps({"draft": "invalid semantic draft"}),
            json.dumps({"draft": "corrected semantic draft"}),
        ],
        finalize=finalize,
    )

    assert result["state"] == "succeeded"
    assert len(drafts) == len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


def test_parse_correction_exhaustion_never_submits_a_third_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence = _execute(
        monkeypatch,
        response_contents=["not-json", "still-not-json"],
    )

    assert result["state"] == "failed"
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


def test_finalizer_correction_exhaustion_never_submits_a_third_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def reject(**_kwargs):
        nonlocal calls
        calls += 1
        raise skill_finalization_runtime.SkillFinalizationError(
            "finalizer_rejected", errors=("retrieval_strategy_intent_duplicate",)
        )

    result, requests, evidence = _execute(
        monkeypatch,
        response_contents=[
            json.dumps({"draft": "first invalid draft"}),
            json.dumps({"draft": "second invalid draft"}),
        ],
        finalize=reject,
    )

    assert result["state"] == "failed"
    assert calls == len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


def test_parse_correction_then_finalizer_rejection_never_submits_a_third_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(**_kwargs):
        raise skill_finalization_runtime.SkillFinalizationError(
            "finalizer_rejected", errors=("retrieval_strategy_intent_duplicate",)
        )

    result, requests, evidence = _execute(
        monkeypatch,
        response_contents=[
            "not-json",
            json.dumps({"draft": "parse-corrected-but-finalizer-invalid"}),
        ],
        finalize=reject,
    )

    assert result["state"] == "failed"
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


def test_finalizer_runtime_failure_does_not_trigger_model_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_runtime(**_kwargs):
        raise skill_finalization_runtime.SkillFinalizationError("finalizer_execution_failed")

    result, requests, evidence = _execute(
        monkeypatch,
        response_contents=[json.dumps({"draft": "valid semantic draft"})],
        finalize=fail_runtime,
    )

    assert result["state"] == "failed"
    assert len(requests) == 1
    assert [row["kind"] for row in evidence] == ["primary"]


def test_provider_http_failure_does_not_trigger_model_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence = _execute(
        monkeypatch,
        response_contents=["rejected"],
        status_codes=[400],
    )

    assert result["state"] == "failed"
    assert len(requests) == 1
    assert [row["kind"] for row in evidence] == ["primary"]

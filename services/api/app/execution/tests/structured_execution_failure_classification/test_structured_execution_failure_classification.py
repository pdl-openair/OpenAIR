from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from app.execution import durable_skill_runtime, skill_finalization_runtime
from app.modules.provider_transport_evidence import ProviderHttpAttempt


def _sha256(schema: dict) -> str:
    encoded = json.dumps(
        schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _skill() -> dict:
    input_schema = {
        "type": "object",
        "required": ["source"],
        "properties": {"source": {"type": "string"}},
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
        "skillId": "generic_failure_classification",
        "displayName": "Generic Failure Classification",
        "skillPath": "skills/test/generic-failure-classification",
        "sourceContract": {
            "structured_output": {
                "contract_version": 1,
                "guard_version": durable_skill_runtime.OPENTRAJECTORY_STRUCTURED_GUARD_VERSION,
            },
            "frozenSchemas": {
                "input": {"sha256": _sha256(input_schema), "schema": input_schema},
                "output": {"sha256": _sha256(output_schema), "schema": output_schema},
            },
            "frozenFinalization": {
                "contractVersion": 1,
                "entrypoint": {"module": "scripts.finalize", "callable": "finalize"},
                "codeSha256": "a" * 64,
                "draftSchema": {
                    "path": "draft.schema.json",
                    "sha256": _sha256(draft_schema),
                    "schema": draft_schema,
                },
            },
        },
    }


def _policy(recovery_attempts: int) -> dict:
    return {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "classification-model",
        "modelProfile": "terra",
        "reasoningEffort": "none",
        "maxOutputTokens": 1000,
        "readTimeoutSeconds": 30.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": recovery_attempts,
    }


def _execute(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response_contents: list[str],
    recovery_attempts: int,
    status_codes: list[int] | None = None,
    finalize=None,
    with_finalizer: bool = True,
) -> dict:
    skill = _skill()
    if not with_finalizer:
        del skill["sourceContract"]["frozenFinalization"]
    runtime = {
        **skill,
        "runtimeKind": "llm_reasoning",
        "adapterId": "huiyan_chat_completions",
        "modelProfile": None,
    }
    contents = list(response_contents)
    statuses = list(status_codes or [200] * len(contents))

    class Attempts:
        def begin(self, **kwargs) -> ProviderHttpAttempt:
            return ProviderHttpAttempt(
                attempt_id="attempt-primary",
                capability_call_id=str(kwargs["capability_call_id"]),
                attempt_number=1,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
            )

        def begin_sub_attempt(self, **kwargs) -> ProviderHttpAttempt:
            return ProviderHttpAttempt(
                attempt_id="attempt-correction",
                capability_call_id=str(kwargs["capability_call_id"]),
                attempt_number=2,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
                evidence_kind=str(kwargs["evidence_kind"]),
                adapter_name=str(kwargs["adapter_name"]),
            )

        def mark_io_started(self, _attempt: ProviderHttpAttempt) -> None:
            return None

        def finish(self, _attempt: ProviderHttpAttempt, **_kwargs) -> None:
            return None

    class Response:
        headers: dict[str, str] = {}

        def __init__(self, content: str, status_code: int) -> None:
            self.status_code = status_code
            self.content = json.dumps({
                "choices": [{"message": {"content": content}}],
                "model": "classification-model",
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

        def post(self, _url: str, **_kwargs) -> Response:
            if not contents:
                raise AssertionError("unexpected Provider request")
            return Response(contents.pop(0), statuses.pop(0))

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
    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", Attempts)
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

    return durable_skill_runtime.execute_production_durable_skill({
        "skillId": skill["skillId"],
        "parameters": {"source": "frozen input"},
        "frozenGoal": "Classify one generic structured result.",
        "resolvedExecutionPolicy": _policy(recovery_attempts),
        "providerEvidence": {
            "capabilityCallId": "call-failure-classification",
            "attemptNumber": 1,
        },
    })


@pytest.mark.parametrize(
    ("content", "expected_stage"),
    [
        ("not-json", "json_parse_failed"),
        (json.dumps({"wrong": "schema"}), "json_schema_failed"),
    ],
)
def test_completed_response_keeps_parse_and_schema_stages_distinct(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    expected_stage: str,
) -> None:
    result = _execute(
        monkeypatch,
        response_contents=[content],
        recovery_attempts=0,
        with_finalizer=False,
    )

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_response_contract_invalid"
    assert result["failedStage"] == expected_stage


def test_parse_correction_exhaustion_has_recoverable_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _execute(
        monkeypatch,
        response_contents=["not-json", "still-not-json"],
        recovery_attempts=1,
    )

    assert result["failureClassification"] == "provider_structured_output_correction_exhausted"
    assert result["failedStage"] == "json_parse_failed"


def test_finalizer_correction_exhaustion_has_recoverable_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(**_kwargs):
        raise skill_finalization_runtime.SkillFinalizationError(
            "finalizer_rejected", errors=("registered_finalizer_rejected",)
        )

    result = _execute(
        monkeypatch,
        response_contents=[
            json.dumps({"draft": "first invalid draft"}),
            json.dumps({"draft": "second invalid draft"}),
        ],
        recovery_attempts=1,
        finalize=reject,
    )

    assert result["failureClassification"] == "provider_structured_output_correction_exhausted"
    assert result["failedStage"] == "skill_finalization_failed"


def test_finalizer_infrastructure_failure_is_not_provider_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_runtime(**_kwargs):
        raise skill_finalization_runtime.SkillFinalizationError("finalizer_execution_failed")

    result = _execute(
        monkeypatch,
        response_contents=[json.dumps({"draft": "valid semantic draft"})],
        recovery_attempts=1,
        finalize=fail_runtime,
    )

    assert result["failureClassification"] == "skill_finalization_runtime_failed"
    assert result["failedStage"] == "skill_finalization_failed"


def test_provider_http_rejection_is_not_schema_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _execute(
        monkeypatch,
        response_contents=["rejected"],
        status_codes=[400],
        recovery_attempts=1,
    )

    assert result["failureClassification"] == "provider_rejected_request"
    assert result["failedStage"] == "provider_http_response"
    assert result["failedStage"] != "json_schema_failed"

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.execution import durable_skill_runtime
from app.modules.provider_transport_evidence import ProviderHttpAttempt


REPO_ROOT = Path(__file__).resolve().parents[6]


def _schema_sha256(schema: dict[str, Any]) -> str:
    encoded = json.dumps(
        schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _formal_output_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "DurableScientificSelection",
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "selectedId", "assessment"],
        "properties": {
            "kind": {"type": "string", "const": "selection"},
            "selectedId": {"type": "string", "pattern": "^candidate-[ab]$"},
            "assessment": {
                "type": "object",
                "additionalProperties": False,
                "required": ["confidence", "label"],
                "properties": {
                    "confidence": {"type": "number"},
                    "label": {
                        "anyOf": [
                            {"type": "string", "enum": ["supported"]},
                            {"type": "string", "const": "uncertain"},
                        ]
                    },
                },
            },
        },
    }


def _fresh_native_schema(schema_class: str) -> dict[str, Any]:
    schema = _formal_output_schema()
    if schema_class in {"$defs-reference", "definitions-reference"}:
        keyword = "$defs" if schema_class.startswith("$defs") else "definitions"
        assessment = schema["properties"]["assessment"]
        schema[keyword] = {"Assessment": assessment}
        schema["properties"]["assessment"] = {
            "$ref": f"#/{keyword}/Assessment"
        }
    elif schema_class == "array-items-bounds":
        schema["properties"]["tags"] = {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 3,
        }
    elif schema_class == "string-number-bounds":
        schema["properties"]["selectedId"].update({"minLength": 1, "maxLength": 40})
        schema["properties"]["assessment"]["properties"]["confidence"].update(
            {"minimum": 0, "maximum": 1}
        )
    elif schema_class == "type-union":
        schema["properties"]["assessment"]["properties"]["label"] = {
            "type": ["string", "null"],
            "enum": ["supported", None],
        }
    else:  # pragma: no cover - test fixture misuse
        raise AssertionError(f"unknown schema class: {schema_class}")
    return schema


def _runtime_skill(output_schema: dict[str, Any] | None) -> dict[str, Any]:
    source_contract: dict[str, Any]
    if output_schema is not None:
        input_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["source"],
            "properties": {"source": {"type": "string", "minLength": 1}},
        }
        source_contract = {
            "structured_output": {
                "contract_version": 1,
                "guard_version": durable_skill_runtime.OPENTRAJECTORY_STRUCTURED_GUARD_VERSION,
            },
            "frozenSchemas": {
                "input": {"sha256": _schema_sha256(input_schema), "schema": input_schema},
                "output": {
                    "sha256": _schema_sha256(output_schema),
                    "schema": output_schema,
                },
            },
        }
    else:
        source_contract = {
            "runtime_contract": {
                "kind": "llm_reasoning",
                "requires_external_action": False,
                "adapter_id": "huiyan_chat_completions",
                "evidence_contract_version": "none",
            }
        }
    return {
        "skillId": "schema_adoption_fixture" if output_schema is not None else "plain_durable_fixture",
        "displayName": "Durable Schema Adoption Fixture",
        "skillPath": "skills/test/durable-schema-adoption-fixture",
        "sourceContract": source_contract,
        "runtimeKind": "llm_reasoning",
        "adapterId": "huiyan_chat_completions",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    }


def _execute_with_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    output_schema: dict[str, Any] | None,
    execution_path: str = "direct",
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[list[dict[str, Any]]],
]:
    skill = _runtime_skill(output_schema)
    metadata = {
        "output_file": "result.json" if output_schema is not None else "result.md",
        "llm": {"temperature": 0, "max_completion_tokens": 800},
    }
    provider_output = (
        {
            "kind": "selection",
            "selectedId": "candidate-a",
            "assessment": {"confidence": 0.75, "label": "supported"},
        }
        if output_schema is not None
        else {"title": "Plain result", "content": "No schema was requested."}
    )
    captured: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    adapter_scientific_messages: list[list[dict[str, Any]]] = []
    active_attempt_id: str | None = None

    class EvidenceRepository:
        def begin(self, **kwargs: Any) -> ProviderHttpAttempt:
            attempt = ProviderHttpAttempt(
                attempt_id="attempt-schema-adoption",
                capability_call_id=str(kwargs["capability_call_id"]),
                attempt_number=1,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
            )
            lifecycle.append({"event": "begin", "attemptId": attempt.attempt_id})
            return attempt

        def begin_sub_attempt(self, **kwargs: Any) -> ProviderHttpAttempt:
            attempt = ProviderHttpAttempt(
                attempt_id="attempt-schema-adoption",
                capability_call_id=str(kwargs["capability_call_id"]),
                attempt_number=1,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
            )
            lifecycle.append({"event": "begin_sub_attempt", "attemptId": attempt.attempt_id})
            return attempt

        def mark_io_started(self, attempt: ProviderHttpAttempt) -> None:
            nonlocal active_attempt_id
            active_attempt_id = attempt.attempt_id
            lifecycle.append({"event": "io_started", "attemptId": attempt.attempt_id})

        def finish(self, attempt: ProviderHttpAttempt, **kwargs: Any) -> None:
            assert active_attempt_id == attempt.attempt_id
            lifecycle.append(
                {
                    "event": "finish",
                    "attemptId": attempt.attempt_id,
                    "outcome": kwargs["outcome"],
                }
            )

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        content = json.dumps(
            {
                "id": "provider-schema-adoption-once",
                "model": "schema-adoption-model",
                "choices": [{"message": {"content": json.dumps(provider_output)}}],
            }
        ).encode("utf-8")

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def post(self, url: str, *, headers: dict[str, str], content: bytes) -> Response:
            assert active_attempt_id is not None
            lifecycle.append({"event": "post", "attemptId": active_attempt_id})
            captured.append(
                {"url": url, "headers": headers, "body": json.loads(content.decode("utf-8"))}
            )
            return Response()

    real_apply_provider_schema_transport = (
        durable_skill_runtime.apply_provider_schema_transport
    )

    def record_adapter_input(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = kwargs["payload"]
        adapter_scientific_messages.append(deepcopy(payload["messages"]))
        return real_apply_provider_schema_transport(*args, **kwargs)

    monkeypatch.setattr(
        durable_skill_runtime,
        "_read_skill_contract",
        lambda _skill: (metadata, "Choose the supported scientific candidate."),
    )
    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", EvidenceRepository)
    monkeypatch.setattr(
        durable_skill_runtime,
        "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_record_durable_skill_usage",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "apply_provider_schema_transport",
        record_adapter_input,
    )
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
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "schema-adoption-model")

    request = {
            "skillId": skill["skillId"],
            "parameters": {"source": "frozen scientific input"},
            "frozenGoal": "Select one supported candidate.",
            "frozenConstraints": ["Use only frozen evidence."],
            "controllerInputContext": [{"kind": "frozen", "value": "context"}],
            "logicalProviderId": "openai_primary",
            "modelProfile": "terra",
            "providerEvidence": {
                "capabilityCallId": "call-schema-adoption",
                "attemptNumber": 1,
            },
        }
    if execution_path == "nested":
        monkeypatch.setattr(
            durable_skill_runtime,
            "resolve_registered_durable_skill_runtime",
            lambda _skill_id: deepcopy(skill),
        )
        monkeypatch.setattr(
            durable_skill_runtime,
            "resolve_durable_llm_execution_policy",
            lambda _runtime: None,
        )
        result = durable_skill_runtime.execute_new_durable_llm_child(
            skill_id=skill["skillId"],
            parameters=request["parameters"],
            parent_capability_call_id="call-schema-adoption",
            adapter_name="nested-schema-adoption-test",
        )
    else:
        result = durable_skill_runtime.execute_production_durable_skill(
            request,
            prepared_runtime=skill,
        )
    return result, captured, lifecycle, adapter_scientific_messages


@pytest.mark.parametrize(
    "schema_class",
    [
        "$defs-reference",
        "definitions-reference",
        "array-items-bounds",
        "string-number-bounds",
        "type-union",
    ],
)
def test_representative_schema_classes_reach_huiyan_json_mode(
    monkeypatch: pytest.MonkeyPatch,
    schema_class: str,
) -> None:
    result, captured, lifecycle, _messages = _execute_with_fake_http(
        monkeypatch,
        output_schema=_fresh_native_schema(schema_class),
    )

    assert result["state"] == "succeeded"
    assert len(captured) == 1
    assert captured[0]["body"]["response_format"] == {"type": "json_object"}
    assert [event["event"] for event in lifecycle] == [
        "begin",
        "io_started",
        "post",
        "finish",
    ]


def test_schema_with_many_optional_fields_runs_and_is_validated_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {
            f"optional{index}": {"type": "string"}
            for index in range(6)
        },
    }

    result, captured, lifecycle, _messages = _execute_with_fake_http(
        monkeypatch,
        output_schema=schema,
    )

    assert result["state"] == "succeeded"
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert any(warning.startswith("output_contract_gap:") for warning in protocol["warnings"])
    assert len(captured) == 1
    assert captured[0]["body"]["response_format"] == {"type": "json_object"}
    assert any(event["event"] == "post" for event in lifecycle)

    succeeded, captured, lifecycle, _messages = _execute_with_fake_http(
        monkeypatch,
        output_schema=_formal_output_schema(),
    )
    assert succeeded["state"] == "succeeded"
    assert len(captured) == 1
    assert any(event["event"] == "post" for event in lifecycle)


def test_nested_resource_scope_is_not_a_provider_transport_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {
        "$id": "https://example.test/root",
        "$defs": {"Value": {"type": "string"}},
        "type": "object",
        "additionalProperties": False,
        "required": ["nested"],
        "properties": {
            "nested": {
                "$id": "nested-resource",
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {"value": {"$ref": "#/$defs/Value"}},
            }
        },
    }

    result, captured, lifecycle, _messages = _execute_with_fake_http(
        monkeypatch,
        output_schema=schema,
    )

    assert result["state"] == "succeeded"
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert any(warning.startswith("output_contract_gap:") for warning in protocol["warnings"])
    assert len(captured) == 1
    assert captured[0]["body"]["response_format"] == {"type": "json_object"}
    assert any(event["event"] == "post" for event in lifecycle)


@pytest.mark.parametrize("execution_path", ["direct", "nested"])
@pytest.mark.parametrize(
    "schema_path",
    [
        "skills/pvd/pvd-research-record-structuring/provider.schema.json",
    ],
)
def test_schema_with_not_reaches_provider_and_is_validated_locally(
    monkeypatch: pytest.MonkeyPatch,
    execution_path: str,
    schema_path: str,
) -> None:
    schema = json.loads((REPO_ROOT / schema_path).read_text(encoding="utf-8"))

    result, captured, lifecycle, _messages = _execute_with_fake_http(
        monkeypatch,
        output_schema=schema,
        execution_path=execution_path,
    )

    assert result["state"] == "succeeded"
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert any(warning.startswith("output_contract_gap:") for warning in protocol["warnings"])
    assert len(captured) == 1
    assert captured[0]["body"]["response_format"] == {"type": "json_object"}
    assert any(event["event"] == "post" for event in lifecycle)


def test_canonical_durable_request_without_schema_does_not_invent_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, captured, lifecycle, adapter_scientific_messages = _execute_with_fake_http(
        monkeypatch,
        output_schema=None,
    )

    assert result["state"] == "succeeded"
    assert len(captured) == 1
    final_payload = captured[0]["body"]
    messages = final_payload["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    final_payload_json = json.dumps(final_payload, sort_keys=True)
    assert "openair.provider_schema.v1" not in final_payload_json
    assert "OpenAIR structured-output transport." not in final_payload_json
    assert "providerSchema" not in final_payload_json
    assert "frozenOutputSchema" not in final_payload_json
    assert '"type": "json_schema"' not in final_payload_json
    assert adapter_scientific_messages == []
    assert lifecycle == [
        {"event": "begin", "attemptId": "attempt-schema-adoption"},
        {"event": "io_started", "attemptId": "attempt-schema-adoption"},
        {"event": "post", "attemptId": "attempt-schema-adoption"},
        {
            "event": "finish",
            "attemptId": "attempt-schema-adoption",
            "outcome": "response_received",
        },
    ]

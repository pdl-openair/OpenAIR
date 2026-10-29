from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.execution import durable_skill_runtime as runtime
from app.execution import skill_finalization_runtime
from app.models.result_protocol import result_protocol_usable
from app.modules.artifact_repository import _row_to_dict
from app.modules.provider_transport_evidence import ProviderHttpAttempt


def frozen_schema(schema: dict) -> dict:
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"schema": schema, "sha256": hashlib.sha256(encoded).hexdigest()}


@pytest.mark.parametrize("minimal", [False, True])
@pytest.mark.parametrize("judgment", ["unsupported", " DEFER "])
def test_real_reasoning_runtime_delivers_gaps_without_mutating_input(
    monkeypatch,
    minimal: bool,
    judgment: str,
) -> None:
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["finding", "judgment"],
        "properties": {
            "finding": {"type": "string"},
            "judgment": {"enum": ["release", "defer"]},
        },
    }
    output_schema = {
        "type": "object",
        "required": ["finding"],
        "properties": {"finding": {"type": "string"}},
        "additionalProperties": False,
    }
    skill = {
        "skillId": "scientific_input_boundary",
        "skillPath": "skills/test/scientific-input",
        "sourceContract": {
            "frozenSchemas": {
                "input": frozen_schema(input_schema),
                "output": frozen_schema(output_schema),
            },
            "frozenFinalization": {
                "contractVersion": 1,
                "codeSha256": "a" * 64,
                "entrypoint": {"module": "scripts.finalize", "callable": "finalize"},
                "draftSchema": {"path": "draft.schema.json", **frozen_schema(output_schema)},
            },
        },
    }
    parameters = {"finding": "valid", "judgment": judgment, "extra": "保留的上下文"}
    before = deepcopy(parameters)
    captured: list[dict] = []
    finalized: list[dict] = []
    metadata = {"output_file": "result.json"}
    if minimal:
        metadata["llm"] = {"provider_message_contract": "scientific_minimal_v1"}
    config = SimpleNamespace(
        adapter="huiyan_chat_completions",
        model="offline-model",
        timeoutSeconds=60,
        baseUrl="https://offline.invalid",
        apiKey="offline-only",
    )
    monkeypatch.setattr(
        runtime,
        "load_provider_registry",
        lambda: SimpleNamespace(require=lambda _id: SimpleNamespace(maxOutputChars=12000)),
    )
    monkeypatch.setattr(
        runtime, "resolve_provider_runtime_config", lambda *_args, **_kwargs: config
    )
    monkeypatch.setattr(
        runtime,
        "_read_skill_contract",
        lambda _skill: (metadata, "Preserve scientific uncertainty."),
    )
    monkeypatch.setattr(runtime, "_record_durable_skill_usage", lambda **_kwargs: None)

    class EvidenceRepository:
        def begin(self, **kwargs):
            return ProviderHttpAttempt(
                attempt_id="offline-primary",
                capability_call_id="offline-call",
                attempt_number=1,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="offline",
            )

        def mark_io_started(self, _attempt):
            return None

        def finish(self, _attempt, **_kwargs):
            return None

    class Client:
        def __init__(self, **_kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, *, content, **_kwargs):
            captured.append(json.loads(content))
            body = json.dumps(
                {"choices": [{"message": {"content": '{"finding":"valid"}'}}]}
            ).encode()
            return SimpleNamespace(
                status_code=200, headers={}, content=body, raise_for_status=lambda: None
            )

    def finalize(**kwargs):
        finalized.append(deepcopy(kwargs))
        return skill_finalization_runtime.FinalizedSkillOutput(
            output={"finding": "valid"}, status="succeeded"
        )

    monkeypatch.setattr(runtime, "ProviderHttpAttemptRepository", EvidenceRepository)
    monkeypatch.setattr(runtime.httpx, "Client", Client)
    monkeypatch.setattr(
        skill_finalization_runtime, "finalize_and_validate_registered_output", finalize
    )
    result = runtime._execute_durable_huiyan_skill(
        {
            "parameters": parameters,
            "logicalProviderId": "openai_primary",
            "providerEvidence": {"capabilityCallId": "offline-call", "attemptNumber": 1},
        },
        skill,
        runtime={
            "runtimeKind": "llm_reasoning",
            "adapterId": "huiyan_chat_completions",
            "modelProfile": "terra",
        },
    )
    assert result["state"] == "succeeded", result
    assert parameters == before
    assert len(captured) == 1
    user = json.loads(captured[0]["messages"][1]["content"])
    expected = before
    assert user["scientificInput" if minimal else "selectedParameters"] == expected
    presentation = result["artifactPayload"]["presentation"]["metadata"]
    assert presentation["resultProtocol"]["status"] == "ok"
    assert not presentation["resultProtocol"]["warnings"]
    assert not presentation.get("warnings")
    public_artifact = _row_to_dict({"metadata": presentation, "kind": "document"})
    assert public_artifact["warnings"] == presentation["resultProtocol"]["warnings"]
    assert result_protocol_usable(public_artifact["resultProtocol"])
    assert finalized[0]["input_payload"] == expected

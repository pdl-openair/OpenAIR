from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import httpx
import pytest
import yaml

from app.execution import durable_skill_runtime
from app.execution import skill_finalization_runtime
from app.execution.skill_finalization_runtime import (
    SkillFinalizationError,
    _execute_finalizer_subprocess,
    build_frozen_finalization_contract,
    finalize_and_validate_registered_output,
)
from app.modules.repo_paths import repo_root
from app.modules.provider_transport_evidence import ProviderHttpAttempt



@pytest.fixture(autouse=True)
def block_external_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("this suite permits no Provider/network/database calls")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "post", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    import psycopg
    monkeypatch.setattr(psycopg, "connect", forbidden)


def _write_finalizer(skill_root: Path, body: str) -> dict:
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "finalize.py").write_text(body, encoding="utf-8")
    return {"entrypoint": {"module": "scripts.finalize", "callable": "finalize"}}


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


def _execute_generic_frozen_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict, list[dict]]:
    skill = _generic_frozen_finalizer_skill()
    requests: list[dict] = []

    class Attempts:
        def begin(self, **kwargs) -> ProviderHttpAttempt:
            return ProviderHttpAttempt(
                attempt_id="attempt-generic-finalizer",
                capability_call_id=str(kwargs["capability_call_id"]),
                attempt_number=1,
                request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
                provider_endpoint_identity="test:endpoint",
            )

        def mark_io_started(self, _attempt: ProviderHttpAttempt) -> None:
            return None

        def finish(self, _attempt: ProviderHttpAttempt, **_kwargs) -> None:
            return None

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        @property
        def content(self) -> bytes:
            return json.dumps(self.json(), separators=(",", ":")).encode("utf-8")

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({"draft": "raw draft"})}}]}

    class Client:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, content: bytes, **_kwargs) -> Response:
            requests.append(json.loads(content))
            return Response()

    runtime = {
        **skill,
        "runtimeKind": "llm_reasoning",
        "adapterId": "huiyan_chat_completions",
        "modelProfile": "terra",
    }
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
    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", Attempts)
    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=Client,
            Timeout=httpx.Timeout,
            HTTPStatusError=httpx.HTTPStatusError,
            HTTPError=httpx.HTTPError,
        ),
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = durable_skill_runtime.execute_production_durable_skill({
        "skillId": skill["skillId"],
        "parameters": {"source": "frozen input"},
        "frozenGoal": "Finalize one generic draft.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
        "providerEvidence": {
            "capabilityCallId": "call-generic-finalizer",
            "attemptNumber": 1,
        },
    })
    return result, requests


def test_direct_production_frozen_finalizer_builds_only_finalized_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def execute_finalizer(**kwargs):
        calls.append(kwargs)
        return skill_finalization_runtime.FinalizedSkillOutput(
            output={"canonical": "final output"}, status="succeeded"
        )

    monkeypatch.setattr(
        skill_finalization_runtime, "execute_registered_finalizer", execute_finalizer
    )
    result, requests = _execute_generic_frozen_finalizer(monkeypatch)

    assert result["state"] == "succeeded"
    assert len(calls) == 1
    assert calls[0]["draft_payload"] == {"draft": "raw draft"}
    assert calls[0]["input_payload"] == {"source": "frozen input"}
    artifact = base64.b64decode(result["artifactPayload"]["contentBase64"])
    assert json.loads(artifact) == {"canonical": "final output"}
    assert b"raw draft" not in artifact


def test_valid_partial_finalization_keeps_accepted_contribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        skill_finalization_runtime, "execute_registered_finalizer",
        lambda **_kwargs: skill_finalization_runtime.FinalizedSkillOutput(
            output={"canonical": "accepted contribution"}, status="degraded", errors=("one source unavailable",)
        ),
    )
    result, _requests = _execute_generic_frozen_finalizer(monkeypatch)
    assert result["state"] == "succeeded"
    artifact = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    assert artifact == {"canonical": "accepted contribution"}
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert not protocol.get("quarantined_items")
    assert "one source unavailable" in protocol["warnings"]


@pytest.mark.parametrize("code", [
    "finalizer_rejected", "finalizer_result_invalid",
    "finalizer_execution_failed", "finalizer_execution_timeout",
])
def test_direct_production_frozen_finalizer_rejection_preserves_unfinalized_draft(
    monkeypatch: pytest.MonkeyPatch, code: str,
) -> None:
    def reject(**_kwargs):
        raise SkillFinalizationError(code)

    monkeypatch.setattr(skill_finalization_runtime, "execute_registered_finalizer", reject)
    result, _requests = _execute_generic_frozen_finalizer(monkeypatch)

    assert result["state"] == "succeeded"
    artifact = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    assert artifact == {"draft": "raw draft"}
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert protocol["quarantined_items"] == [{"path": "$", "reason": "finalization_contract_gap"}]


def test_direct_production_invalid_final_schema_preserves_partial_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_output(**_kwargs):
        return skill_finalization_runtime.FinalizedSkillOutput(
            output={"unexpected": "not canonical"}, status="succeeded"
        )

    monkeypatch.setattr(
        skill_finalization_runtime, "execute_registered_finalizer", invalid_output
    )
    result, _requests = _execute_generic_frozen_finalizer(monkeypatch)

    assert result["state"] == "succeeded"
    artifact = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    assert artifact == {"unexpected": "not canonical"}
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert protocol["quarantined_items"] == [{"path": "$", "reason": "finalization_contract_gap"}]


def test_subprocess_json_bridge_is_locale_independent_and_surrogate_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _write_finalizer(
        tmp_path,
        "def finalize(input_payload, model_output):\n"
        "    return {'value': input_payload['value']}\n",
    )
    monkeypatch.setenv("PYTHONIOENCODING", "ascii:strict")
    value = "AlScN ∼ 20% − 中文 \ud800"

    result = _execute_finalizer_subprocess(
        skill_root=tmp_path,
        contract=contract,
        input_payload={"value": value},
        draft_payload={},
    )

    assert result == {"value": value}


def test_subprocess_exception_preserves_bounded_machine_facts_without_input_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _write_finalizer(
        tmp_path,
        "def finalize(input_payload, model_output):\n"
        "    raise RuntimeError('private=' + input_payload['secret'])\n",
    )
    monkeypatch.setenv("PYTHONIOENCODING", "ascii:strict")

    with pytest.raises(SkillFinalizationError) as captured:
        _execute_finalizer_subprocess(
            skill_root=tmp_path,
            contract=contract,
            input_payload={"secret": "DO_NOT_LEAK"},
            draft_payload={},
        )

    error = captured.value
    assert error.code == "finalizer_execution_failed"
    assert len(error.errors) == 1
    facts = error.errors[0]
    assert facts["code"] == "finalizer_child_exception"
    assert facts["exceptionType"] == "RuntimeError"
    assert facts["source"]["file"] == "scripts/finalize.py"
    assert facts["source"]["function"] == "finalize"
    assert isinstance(facts["source"]["line"], int)
    assert len(facts["messageSha256"]) == 64
    assert facts["messageChars"] > 0
    assert "DO_NOT_LEAK" not in json.dumps(facts, ensure_ascii=True)


def test_screening_registered_finalizer_survives_ascii_only_child_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = repo_root()
    relative = "skills/opentrajectory/opentrajectory-source-screening-selection"
    skill_root = root / relative
    skill_yaml = yaml.safe_load((skill_root / "skill.yaml").read_text(encoding="utf-8"))
    frozen = build_frozen_finalization_contract(
        relative,
        skill_yaml["finalization"],
        repo_root=root,
    )
    final_schema = json.loads((skill_root / "output.schema.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("PYTHONIOENCODING", "ascii:strict")

    candidate = {
        "candidate_id": "openalex_1_1",
        "title": "AlScN screening candidate",
        "abstract": "Promising candidates with ∼ 20 % Sc composition; 中文说明.",
        "doi": "10.1103/physrevmaterials.8.095001",
        "provenance": [{"source_name": "openalex"}],
    }
    input_payload = {
        "task_id": "finalizer-wire-regression",
        "user_query": "Find AlScN sputtering process evidence.",
        "research_context": {"research_goal": "Traceable process evidence"},
        "candidates": [candidate],
    }
    draft_payload = {
        "judgments": [{
            "candidate_id": "openalex_1_1",
            "decision": "supporting",
            "reading_priority": 55,
            "reason": "The abstract contains relevant sputtering context.",
        }]
    }

    finalized = finalize_and_validate_registered_output(
        repo_root=root,
        skill_path=relative,
        frozen_contract=frozen,
        input_payload=input_payload,
        draft_payload=draft_payload,
        final_schema=final_schema,
    )

    selected = finalized.output["topk_selection"]["selected_for_p03"]
    assert finalized.status == "succeeded"
    assert selected[0]["candidate_id"] == "openalex_1_1"
    assert selected[0]["abstract"] == candidate["abstract"]


def test_finalizer_runtime_failure_classification_crosses_durable_boundaries() -> None:
    from app.execution.durable_multiagent_orchestrator import _failure_classification

    failure = {"failureClassification": "skill_finalization_runtime_failed"}
    assert _failure_classification(failure) == "skill_finalization_runtime_failed"


def test_collaboration_repository_accepts_finalizer_runtime_failure_before_persistence() -> None:
    from app.modules.collaboration_repository import CollaborationRepository

    class ReachedPersistence(RuntimeError):
        pass

    class DurableStub:
        def _connect(self):
            raise ReachedPersistence("classification accepted")

    repository = object.__new__(CollaborationRepository)
    repository._durable = DurableStub()

    with pytest.raises(ReachedPersistence, match="classification accepted"):
        repository.finish_capability_invocation(
            call_id="call_1",
            owner_id="owner_1",
            workspace_id="workspace_1",
            worker_id="worker_1",
            fence_epoch=1,
            state="failed",
            summary="registered finalization runtime failed",
            output_artifact_refs=[],
            failure_classification="skill_finalization_runtime_failed",
        )

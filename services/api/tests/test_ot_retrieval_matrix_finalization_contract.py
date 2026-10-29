from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from app.execution import durable_skill_runtime, skill_finalization_runtime
from app.modules.provider_transport_evidence import (
    ProviderAttemptEvidenceError,
    ProviderHttpAttempt,
)
from app.modules.skill_binding_source import (
    BINDING_CONTRACT_VERSION,
    load_skill_binding_source_rows,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = "skills/opentrajectory/retrieval-matrix-generation"
SKILL_ROOT = REPO_ROOT / SKILL_PATH


class _ReservedAttemptRepository:
    def begin(self, **kwargs) -> ProviderHttpAttempt:
        call_id = str(kwargs["capability_call_id"] or "").strip()
        if not call_id or kwargs["attempt_number"] != 1:
            raise ProviderAttemptEvidenceError("provider_attempt_identity_invalid")
        return ProviderHttpAttempt(
            attempt_id=f"attempt-{call_id}",
            capability_call_id=call_id,
            attempt_number=1,
            request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
            provider_endpoint_identity="test:endpoint",
        )

    def begin_sub_attempt(self, **kwargs) -> ProviderHttpAttempt:
        call_id = str(kwargs["capability_call_id"] or "").strip()
        if not call_id:
            raise ProviderAttemptEvidenceError("provider_attempt_identity_invalid")
        return ProviderHttpAttempt(
            attempt_id=f"attempt-{call_id}-sub",
            capability_call_id=call_id,
            attempt_number=2,
            request_hash=hashlib.sha256(kwargs["request_body"]).hexdigest(),
            provider_endpoint_identity="test:endpoint",
            evidence_kind=str(kwargs["evidence_kind"]),
            adapter_name=str(kwargs["adapter_name"]),
        )

    def finish(self, _attempt: ProviderHttpAttempt, **_kwargs) -> None:
        return None

    def mark_io_started(self, _attempt: ProviderHttpAttempt) -> None:
        return None


def _registry_row() -> dict:
    registry = yaml.safe_load((REPO_ROOT / "skills/registry.yaml").read_text(encoding="utf-8"))
    rows = [
        row
        for row in registry["skills"]
        if row.get("skill_id") == "retrieval_matrix_generation"
    ]
    assert len(rows) == 1
    return rows[0]


def _parameters() -> dict:
    return {
        "intent_understanding": json.loads(
            (
                REPO_ROOT
                / "skills/opentrajectory/structured-requirement-generation/examples/response.json"
            ).read_text(encoding="utf-8")
        ),
        "constraints_criteria": {
            "interpreted_constraints": [
                {
                    "constraint_name": "voltage_window",
                    "original_phrase": "voltage at or below 4.2 V",
                    "constraint_type": "numerical_range",
                    "interpreted_value": "at or below 4.2 V",
                    "candidate_values": [],
                    "semantic_state": "confirmed",
                    "domain_rationale": "The user supplied the voltage boundary.",
                    "downstream_search_terms": ["voltage window", "cutoff voltage 4.2 V"],
                    "evidence_basis": "original user query",
                }
            ],
            "missing_information": [],
            "research_risks": [],
        },
    }


def _semantic_draft() -> dict:
    return {
        "strategies": [
            {
                "search_intent": "core_topic",
                "query_terms": ["scientific agent"],
                "must_include_terms": ["scientific agent"],
                "optional_terms": ["result reliability"],
                "exclude_terms": [],
                "expected_paper_type": "",
                "target_database": "",
                "source_constraints": ["voltage_window"],
                "rationale": "Covers the confirmed research object.",
                "priority": 1,
            }
        ],
        "expanded_query_variants": [
            {
                "query": f'"scientific agent" AND "result reliability {index}"',
                "variant_type": "fallback" if index == 7 else "broad",
                "search_intent": "core_topic",
                "source_constraints": ["voltage_window"],
                "rationale": f"Provides supported query role {index}.",
                "priority": 1 if index < 3 else 2 if index < 6 else 3,
            }
            for index in range(8)
        ],
        "facets": {
            "core_entities": ["scientific agent"],
            "research_object_en": ["scientific agent"],
            "mechanisms": [],
            "target_metrics_en": ["result reliability"],
            "task_context": [],
            "must_have_terms": ["scientific agent"],
            "nice_to_have_terms": ["result reliability"],
            "negative_contexts": [],
            "evaluation_terms": ["result reliability"],
        },
    }


def _binding_evidence() -> dict:
    binding = next(
        row for row in load_skill_binding_source_rows()
        if row["skillId"] == "retrieval_matrix_generation"
    )
    return {
        "bindingId": binding["bindingId"],
        "bindingVersion": binding["bindingVersion"],
        "bindingContractVersion": BINDING_CONTRACT_VERSION,
        "inputSources": binding.get("inputSources"),
        "runtimeInputs": binding.get("runtimeInputs"),
    }


def _resolved_execution_policy() -> dict:
    return {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "frozen-terra-model",
        "modelProfile": "terra",
        "reasoningEffort": "minimal",
        "maxOutputTokens": 6000,
        "readTimeoutSeconds": 90.0,
        "taskBudgetSeconds": 135.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": 1,
    }


def test_registry_freezes_retrieval_semantic_draft_without_changing_ot_model_authority() -> None:
    row = _registry_row()
    assert row["durable_model_profile"] == "terra"
    assert row["durable_llm_execution_policy"] == {
        "contract_version": 1,
        "reasoning_effort": "low",
        "max_output_tokens": 6000,
        "read_timeout_seconds": 90,
        "task_budget_seconds": 135,
        "response_mode": "json_object",
        "load_references": False,
        "structured_output_recovery_attempts": 1,
    }
    assert "model_profile" not in row["durable_llm_execution_policy"]
    assert row["structured_output"] == {
        "contract_version": 1,
        "guard_version": "p06c-v1",
    }

    frozen = skill_finalization_runtime.build_frozen_finalization_contract(
        SKILL_PATH,
        row["finalization"],
        repo_root=REPO_ROOT,
    )
    assert frozen["entrypoint"] == {"module": "scripts.finalize", "callable": "finalize"}
    assert frozen["draftSchema"]["path"] == "model_output.schema.json"
    assert frozen["draftSchema"]["schema"]["title"] == (
        "retrieval-matrix-generation model semantic draft"
    )

    formal = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    assert frozen["draftSchema"]["schema"] != formal
    assert "quality_report" not in frozen["draftSchema"]["schema"]["properties"]


def test_real_runtime_snapshot_exposes_the_registered_retrieval_finalizer() -> None:
    skill = durable_skill_runtime.resolve_registered_durable_skill("retrieval_matrix_generation")
    assert skill is not None
    source = skill["sourceContract"]

    assert source["durable_model_profile"] == "terra"
    assert source["durable_llm_execution_policy"]["structured_output_recovery_attempts"] == 1
    assert source["durable_llm_execution_policy"]["task_budget_seconds"] == 135.0
    assert source["durable_llm_execution_policy"]["model_profile"] is None
    assert source["frozenFinalization"]["entrypoint"] == {
        "module": "scripts.finalize",
        "callable": "finalize",
    }
    assert source["frozenFinalization"]["draftSchema"]["path"] == "model_output.schema.json"
    assert source["frozenSchemas"]["output"]["schema"] == json.loads(
        (SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8")
    )


def test_current_problem_framing_input_is_not_cleared_during_structured_validation() -> None:
    payload = _parameters()
    original = deepcopy(payload)
    schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))

    result = durable_skill_runtime._validate_structured_json(
        payload,
        schema,
        "retrieval input",
        role="input",
    )

    assert result is payload
    assert payload == original


def test_production_path_quarantines_one_malformed_query_and_returns_degraded_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []
    finalizer_calls: list[dict] = []
    finalizer_errors: list[tuple[str, object]] = []
    provider_draft = _semantic_draft()
    provider_draft["expanded_query_variants"][-1]["query"] = (
        '"scientific agent" AND ("result reliability"'
    )

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        @property
        def content(self) -> bytes:
            return json.dumps(self.json(), separators=(",", ":")).encode("utf-8")

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(provider_draft)}}]}

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

    original = skill_finalization_runtime.execute_registered_finalizer

    def counted_execute_finalizer(**kwargs):
        finalizer_calls.append(kwargs)
        try:
            return original(**kwargs)
        except Exception as exc:
            finalizer_errors.append((str(getattr(exc, "code", "")), getattr(exc, "errors", None)))
            raise

    monkeypatch.setattr(
        skill_finalization_runtime,
        "execute_registered_finalizer",
        counted_execute_finalizer,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "ProviderHttpAttemptRepository",
        _ReservedAttemptRepository,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
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
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = durable_skill_runtime.execute_production_durable_skill(
        {
            "skillId": "retrieval_matrix_generation",
            "parameters": _parameters(),
            "frozenGoal": "Generate the retrieval strategy for the confirmed research task.",
            "logicalProviderId": "openai_primary",
            "modelProfile": "terra",
            "resolvedExecutionPolicy": _resolved_execution_policy(),
            "bindingInputEvidence": _binding_evidence(),
            "providerEvidence": {
                "capabilityCallId": "call-ot-retrieval-finalization",
                "attemptNumber": 1,
            },
        }
    )

    assert result["state"] == "succeeded", (result, finalizer_errors, finalizer_calls)
    assert len(requests) == 1
    assert len(finalizer_calls) == 1
    assert finalizer_calls[0]["input_payload"] == _parameters()
    assert finalizer_calls[0]["draft_payload"] == provider_draft

    provider_contract = json.loads(requests[0]["messages"][1]["content"])
    assert provider_contract["scientificInput"] == _parameters()
    provider_schema = provider_contract["outputSchema"]
    assert provider_schema["title"] == "retrieval-matrix-generation model semantic draft"
    assert "quality_report" not in provider_schema["properties"]
    assert "search_matrix" not in provider_schema["properties"]

    artifact = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    assert len(artifact["search_matrix"]) == 7
    assert len(artifact["expanded_query_variants"]) == 7
    assert artifact["search_matrix"][0]["query_terms"] == ["scientific agent"]
    assert all(row["query_terms"] == [] for row in artifact["search_matrix"][1:])
    assert artifact["result_protocol"]["status"] == "degraded"
    assert artifact["result_protocol"]["coverage"]["accepted_query_count"] == 7
    assert any(
        row["reason"] == "boolean_syntax_invalid"
        for row in artifact["result_protocol"]["quarantined_items"]
    )
    metadata = result["artifactPayload"]["presentation"]["metadata"]
    assert metadata["resultProtocol"]["status"] == artifact["result_protocol"]["status"]
    assert (
        metadata["resultProtocol"]["quarantined_items"]
        == artifact["result_protocol"]["quarantined_items"]
    )
    assert metadata["artifactQuality"] == "degraded"
    assert "partial_result_available" in metadata["warnings"]


def test_retrieval_finalizer_quarantines_duplicate_strategy_without_model_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []
    invalid = _semantic_draft()
    invalid["strategies"] = [
        deepcopy(invalid["strategies"][0]),
        deepcopy(invalid["strategies"][0]),
    ]
    responses = [invalid]

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        @property
        def content(self) -> bytes:
            return json.dumps(self.json(), separators=(",", ":")).encode("utf-8")

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": json.dumps(self._payload)}}]
            }

    class Client:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, content: bytes, **_kwargs) -> Response:
            requests.append(json.loads(content))
            if not responses:
                raise AssertionError("unexpected third Provider submission")
            return Response(responses.pop(0))

    monkeypatch.setattr(
        durable_skill_runtime,
        "ProviderHttpAttemptRepository",
        _ReservedAttemptRepository,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
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
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = durable_skill_runtime.execute_production_durable_skill(
        {
            "skillId": "retrieval_matrix_generation",
            "parameters": _parameters(),
            "frozenGoal": "Generate the retrieval strategy for the confirmed research task.",
            "logicalProviderId": "openai_primary",
            "modelProfile": "terra",
            "resolvedExecutionPolicy": _resolved_execution_policy(),
            "bindingInputEvidence": _binding_evidence(),
            "providerEvidence": {
                "capabilityCallId": "call-ot-retrieval-recovery",
                "attemptNumber": 1,
            },
        }
    )

    assert result["state"] == "succeeded", result
    assert len(requests) == 1
    artifact = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    assert len(artifact["search_matrix"]) == 7
    assert len(artifact["expanded_query_variants"]) == 8
    assert artifact["result_protocol"]["status"] == "degraded"
    assert any(
        row["reason"] == "duplicate_search_intent"
        for row in artifact["result_protocol"]["quarantined_items"]
    )
    metadata = result["artifactPayload"]["presentation"]["metadata"]
    assert metadata["resultProtocol"]["status"] == artifact["result_protocol"]["status"]
    assert (
        metadata["resultProtocol"]["quarantined_items"]
        == artifact["result_protocol"]["quarantined_items"]
    )
    assert metadata["artifactQuality"] == "degraded"
    assert "partial_result_available" in metadata["warnings"]

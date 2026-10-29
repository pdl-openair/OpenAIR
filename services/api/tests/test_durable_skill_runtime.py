from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from app.execution import durable_skill_runtime, skill_finalization_runtime
from app.modules.durable_execution import _validated_skill_presentation
from app.modules.provider_transport_evidence import ProviderAttemptEvidenceError, ProviderHttpAttempt


VALID_RESEARCH_CONFIG = """project: Bounded feedback study
schema: sdr-driven
research_type: D
ai_mode: copilot
domain: cs_education
subdomain: feedback
target_venue:
  primary: ''
  tier: ''
resources:
  compute: ''
  data_status: ''
checkpoint_stages: []
minimum_success_criteria:
  min_baselines: 0
  min_ablation_runs: 0
  require_proposed: false
  required_metrics: []
modification_log: []
"""

VALID_LITERATURE_REVIEW = """# Problem - Literature Review

## Search Strategy

Search terms: durable multi-agent research. Included peer-reviewed systems papers; excluded uncited summaries.

## Key Literature Summary

### Durable execution

| Reference | DOI / URL / ID | Claim / Finding | Method | Limitations | Relevance |
|-----------|----------------|----------------|--------|-------------|-----------|
| Temporal documentation | https://docs.temporal.io/workflow-execution | Durable state supports recovery. | Documentation review | Not an empirical comparison. | Execution semantics |

## References

- Temporal. Workflow execution. https://docs.temporal.io/workflow-execution
"""


def test_durable_frozen_schema_inlines_ot_relative_references() -> None:
    frozen = durable_skill_runtime.build_frozen_skill_schema_contract(
        "skills/opentrajectory/evidence-node-extraction-skill"
    )

    input_schema = frozen["input"]["schema"]
    assert input_schema["properties"]["paper_packets"]["items"] != {
        "$ref": "../deepxiv-progressive-reading-skill/output.schema.json#/properties/paper_packets/items"
    }
    assert "../deepxiv-progressive-reading-skill/output.schema.json" not in json.dumps(
        input_schema, ensure_ascii=False
    )


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
            raise ProviderAttemptEvidenceError("provider_sub_attempt_identity_invalid")
        return ProviderHttpAttempt(
            attempt_id=f"sub-attempt-{call_id}",
            capability_call_id=call_id,
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


@pytest.fixture(autouse=True)
def reserved_provider_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", _ReservedAttemptRepository)


def _execute_provider(request: dict) -> dict:
    return durable_skill_runtime.execute_durable_skill({
        **request,
        "providerEvidence": {
            "capabilityCallId": "call-durable-skill-unit",
            "attemptNumber": 1,
        },
    })


def _assume_schema_capability_for_downstream_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate post-transport behavior after a schema-capability check has passed."""
    monkeypatch.setattr(
        durable_skill_runtime,
        "apply_provider_schema_transport",
        lambda _provider, *, payload, **_kwargs: dict(payload),
    )


class _ResponseEvidence:
    status_code = 200
    headers: dict[str, str] = {}

    @property
    def content(self) -> bytes:
        try:
            payload = self.json()
        except (AttributeError, ValueError):
            return b""
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


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
        "title": "GenericFinalizerDraft",
        "type": "object",
        "required": ["draft"],
        "properties": {"draft": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }
    output_schema = {
        "title": "GenericFinalizedOutput",
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
    *,
    finalize,
) -> tuple[dict, list[dict]]:
    _assume_schema_capability_for_downstream_test(monkeypatch)
    skill = _generic_frozen_finalizer_skill()
    requests: list[dict] = []

    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({"draft": "raw draft"})}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
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
    monkeypatch.setattr(durable_skill_runtime, "resolve_registered_durable_skill", lambda _skill_id: skill)
    monkeypatch.setattr(
        durable_skill_runtime,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_read_skill_contract",
        lambda _skill: ({"output_file": "result.json", "llm": {"max_completion_tokens": 1000}}, "Execute."),
    )
    monkeypatch.setattr(skill_finalization_runtime, "finalize_and_validate_registered_output", finalize)
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


def test_direct_production_frozen_finalizer_runs_once_and_builds_artifact_from_finalized_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def execute_finalizer(**kwargs):
        calls.append(kwargs)
        return skill_finalization_runtime.FinalizedSkillOutput(
            output={"canonical": "final output"},
            status="succeeded",
        )

    monkeypatch.setattr(skill_finalization_runtime, "execute_registered_finalizer", execute_finalizer)
    result, requests = _execute_generic_frozen_finalizer(
        monkeypatch,
        finalize=skill_finalization_runtime.finalize_and_validate_registered_output,
    )

    assert result["state"] == "succeeded"
    assert len(calls) == 1
    assert calls[0]["draft_payload"] == {"draft": "raw draft"}
    assert calls[0]["input_payload"] == {"source": "frozen input"}
    assert json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"])) == {
        "canonical": "final output"
    }
    assert b"raw draft" not in base64.b64decode(result["artifactPayload"]["contentBase64"])


def test_registered_claude_code_skill_posts_once_and_returns_durable_artifact_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []

    class Response(_ResponseEvidence):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "id": "provider_once",
                "model": "frozen-terra-model",
                "choices": [{"message": {"content": json.dumps({
                    "title": "Research configuration",
                    "content": VALID_RESEARCH_CONFIG,
                })}}],
            }

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            timeout = kwargs["timeout"]
            assert isinstance(timeout, httpx.Timeout)
            assert timeout.connect == 5.0
            assert timeout.pool == 5.0
            assert timeout.write == 10.0
            assert timeout.read is None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict, content: bytes) -> Response:
            requests.append({"url": url, "headers": headers, "json": json.loads(content)})
            return Response()

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")
    monkeypatch.setenv("OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS", "30")

    result = _execute_provider({
        "skillId": "intent_understanding",
        "parameters": {"focus": "CS feedback safety"},
        "frozenGoal": "Study safety and fairness of generated programming feedback.",
        "frozenConstraints": ["Do not claim unsupported causality."],
        "controllerInputContext": ["Use the confirmed course setting."],
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert result["state"] == "succeeded"
    assert result["relatedSkillId"] == "intent_understanding"
    assert len(requests) == 1
    assert requests[0]["json"]["model"] == "frozen-terra-model"
    assert requests[0]["json"]["max_tokens"] == 8192
    assert requests[0]["json"]["response_format"] == {"type": "json_object"}
    provider_contract = json.loads(requests[0]["json"]["messages"][1]["content"])
    assert provider_contract["outputContract"]["artifact"] == "research-config.yaml"
    assert provider_contract["outputContract"]["mimeType"] == "text/yaml"
    assert provider_contract["outputContract"]["maxOutputChars"] == 50000
    assert "minimum_success_criteria" in provider_contract["outputContract"]["template"]
    assert base64.b64decode(result["artifactPayload"]["contentBase64"]).startswith(b"project: Bounded")
    assert result["artifactPayload"]["presentation"]["metadata"]["runtime"] == "llm_reasoning"
    assert _validated_skill_presentation(result["artifactPayload"]["presentation"])["mimeType"] == "text/yaml"


def test_durable_huiyan_json_mode_request_explicitly_requires_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({
                "title": "Research configuration", "content": VALID_RESEARCH_CONFIG,
            })}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, _endpoint: str, *, content: bytes, **_kwargs) -> Response:
            captured.update(json.loads(content.decode("utf-8")))
            return Response()

    monkeypatch.setattr(durable_skill_runtime, "httpx", SimpleNamespace(
        Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError,
    ))
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    assert _execute_provider({
        "skillId": "intent_understanding", "parameters": {}, "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary", "modelProfile": "terra",
    })["state"] == "succeeded"
    request_input = json.loads(captured["messages"][1]["content"])
    assert request_input["responseRequirement"] == "Return one valid json object only."


def test_intent_understanding_normalizes_a_valid_direct_research_config_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(yaml.safe_load(VALID_RESEARCH_CONFIG))}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            return Response()

    monkeypatch.setattr(durable_skill_runtime, "httpx", SimpleNamespace(
        Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError,
    ))
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "intent_understanding", "parameters": {}, "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary", "modelProfile": "terra",
    })

    assert result["state"] == "succeeded"
    assert result["artifactPayload"]["contentType"] == "text/yaml"
    assert yaml.safe_load(base64.b64decode(result["artifactPayload"]["contentBase64"])) == yaml.safe_load(VALID_RESEARCH_CONFIG)


def test_intent_understanding_normalizes_research_config_with_missing_optional_block() -> None:
    malformed = yaml.safe_load(VALID_RESEARCH_CONFIG)
    malformed.pop("minimum_success_criteria")
    body = {"choices": [{"message": {"content": json.dumps(malformed)}}]}

    artifact = durable_skill_runtime._parse_huiyan_artifact(
        body, skill_id="intent_understanding", max_output_chars=10_000, forbidden_values=[],
    )

    parsed = yaml.safe_load(artifact["content"])
    assert parsed["project"] == "Bounded feedback study"
    assert parsed["minimum_success_criteria"]["min_baselines"] == 0
    assert artifact["content"].strip()


def _valid_structured_requirement(user_query: str) -> dict:
    return {
        "original_user_query": user_query,
        "research_object": "lithium metal battery evidence reliability",
        "target_metrics": ["evidence reliability"],
        "boundary_conditions": ["peer-reviewed sources"],
        "comparison_baseline": None,
        "output_intent": "literature_review",
        "requested_source_scope": None,
        "ambiguities": [],
        "requirement_confidence": 0.9,
    }


def _valid_problem_framing(user_query: str) -> dict:
    return {
        "intent_understanding": {
            "original_user_query": user_query,
            "research_object": "fast-charge battery evidence",
            "target_metrics": [],
            "boundary_conditions": [],
            "comparison_baseline": None,
            "output_intent": "literature_review",
            "requested_source_scope": None,
            "ambiguities": ["The user did not define a charge-rate threshold."],
        },
        "constraints_criteria": {
            "interpreted_constraints": [{
                "constraint_name": "fast_condition",
                "original_phrase": "fast charging",
                "constraint_type": "condition",
                "interpreted_value": None,
                "candidate_values": ["2C"],
                "semantic_state": "ambiguous",
                "domain_rationale": "2C is a possible retrieval enhancement, not a user-confirmed threshold.",
                "downstream_search_terms": ["fast charging", "C-rate"],
                "evidence_basis": "fast charging",
            }],
            "missing_information": [],
            "research_risks": [],
        },
    }


def test_shared_evidence_batch_retries_one_paper_once_and_merges_valid_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    attempts: dict[str, int] = {}
    lock = threading.Lock()

    class Response:
        headers: dict[str, str] = {}

        def __init__(self, status_code: int, payload: dict) -> None:
            self.status_code = status_code
            self.content = json.dumps(payload).encode("utf-8")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, _url: str, *, content: bytes, **_kwargs) -> Response:
            payload = json.loads(content)
            contract = json.loads(payload["messages"][1]["content"])
            packet = contract["selectedParameters"]["paper_packets"][0]
            candidate_id = packet["candidate_id"]
            with lock:
                attempts[candidate_id] = attempts.get(candidate_id, 0) + 1
                ordinal = attempts[candidate_id]
            if candidate_id == "paper-b" and ordinal == 1:
                return Response(503, {"error": "temporary"})
            return Response(200, {
                "choices": [{"message": {"content": json.dumps({
                    "research_records": [{"candidate": candidate_id}]
                })}}]
            })

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_validate_structured_json",
        lambda value, *_args, **_kwargs: value,
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )

    schema = {
        "type": "object",
        "required": ["research_records"],
        "properties": {
            "research_records": {"type": "array", "maxItems": 8, "items": {"type": "object"}}
        },
        "additionalProperties": False,
    }
    parameters = {
        "task_id": "task-evidence-batch",
        "paper_packets": [{"candidate_id": "paper-a"}, {"candidate_id": "paper-b"}],
        "evidence_schema": {"schema_id": "schema-1"},
    }
    base_payload = {
        "messages": [
            {"role": "system", "content": "structured evidence"},
            {"role": "user", "content": json.dumps({
                "selectedParameters": parameters,
                "outputContract": {"frozenOutputSchema": schema},
            })},
        ]
    }
    result = durable_skill_runtime._execute_evidence_batch_requests(
        request={
            "providerEvidence": {
                "capabilityCallId": "call-evidence-batch",
                "attemptNumber": 1,
            }
        },
        skill_id="evidence_node_extraction",
        parameters=parameters,
        provider_parameters=parameters,
        field="research_records",
        base_payload=base_payload,
        draft_schema=schema,
        runtime_config=SimpleNamespace(
            timeoutSeconds=5, adapter="huiyan_chat_completions", model="test-model"
        ),
        endpoint="https://provider.test/v1/chat/completions",
        base_headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        provider_id="openai_primary",
        max_output_chars=50_000,
    )

    assert result["successfulPaperCount"] == 2
    assert result["failedPapers"] == []
    assert [row["candidate"] for row in result["draft"]["research_records"]] == [
        "paper-a", "paper-b"
    ]
    assert attempts == {"paper-a": 1, "paper-b": 2}
    assert result["maxParallel"] == 2


def test_shared_evidence_batch_does_not_replay_transport_uncertain_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    timeouts: list[object] = []

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            timeouts.append(kwargs.get("timeout"))

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, _url: str, **_kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("response observation interrupted")

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_validate_structured_json",
        lambda value, *_args, **_kwargs: value,
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )

    schema = {
        "type": "object",
        "required": ["research_records"],
        "properties": {
            "research_records": {"type": "array", "maxItems": 8, "items": {"type": "object"}}
        },
        "additionalProperties": False,
    }
    parameters = {
        "task_id": "task-evidence-uncertain",
        "paper_packets": [{"candidate_id": "paper-uncertain"}],
        "evidence_schema": {"schema_id": "schema-1"},
    }
    base_payload = {
        "messages": [
            {"role": "system", "content": "structured evidence"},
            {"role": "user", "content": json.dumps({
                "selectedParameters": parameters,
                "outputContract": {"frozenOutputSchema": schema},
            })},
        ]
    }

    result = durable_skill_runtime._execute_evidence_batch_requests(
        request={
            "providerEvidence": {
                "capabilityCallId": "call-evidence-uncertain",
                "attemptNumber": 1,
            }
        },
        skill_id="evidence_node_extraction",
        parameters=parameters,
        provider_parameters=parameters,
        field="research_records",
        base_payload=base_payload,
        draft_schema=schema,
        runtime_config=SimpleNamespace(
            timeoutSeconds=5, adapter="huiyan_chat_completions", model="test-model"
        ),
        endpoint="https://provider.test/v1/chat/completions",
        base_headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        provider_id="openai_primary",
        max_output_chars=50_000,
    )

    assert calls == 1
    assert result["successfulPaperCount"] == 0
    assert result["failedPapers"] == [{
        "paperIndex": 1,
        "candidateId": "paper-uncertain",
        "reason": "transport_uncertain",
        "providerExecutionId": "attempt-call-evidence-uncertain",
    }]
    assert len(timeouts) == 1
    assert isinstance(timeouts[0], httpx.Timeout)
    assert timeouts[0].read is None

def test_shared_evidence_batch_does_not_retry_structurally_invalid_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    attempts: dict[str, int] = {}
    lock = threading.Lock()

    class Response:
        headers: dict[str, str] = {}

        def __init__(self, status_code: int, payload: dict) -> None:
            self.status_code = status_code
            self.content = json.dumps(payload).encode("utf-8")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, _url: str, *, content: bytes, **_kwargs) -> Response:
            payload = json.loads(content)
            contract = json.loads(payload["messages"][1]["content"])
            candidate_id = contract["selectedParameters"]["paper_packets"][0]["candidate_id"]
            with lock:
                attempts[candidate_id] = attempts.get(candidate_id, 0) + 1
            if candidate_id == "paper-bad":
                return Response(200, {
                    "choices": [{"message": {"content": "not-json"}}]
                })
            return Response(200, {
                "choices": [{"message": {"content": json.dumps({
                    "evidence_items": [{"candidate": candidate_id}]
                })}}]
            })

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_validate_structured_json",
        lambda value, *_args, **_kwargs: value,
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )

    schema = {
        "type": "object",
        "required": ["evidence_items"],
        "properties": {
            "evidence_items": {"type": "array", "maxItems": 8, "items": {"type": "object"}}
        },
        "additionalProperties": False,
    }
    parameters = {
        "task_id": "task-pvd-evidence-batch",
        "paper_packets": [{"candidate_id": "paper-good"}, {"candidate_id": "paper-bad"}],
        "evidence_schema": {"schema_id": "schema-1"},
    }
    base_payload = {
        "messages": [
            {"role": "system", "content": "structured evidence"},
            {"role": "user", "content": json.dumps({
                "selectedParameters": parameters,
                "outputContract": {"frozenOutputSchema": schema},
            })},
        ]
    }
    result = durable_skill_runtime._execute_evidence_batch_requests(
        request={
            "providerEvidence": {
                "capabilityCallId": "call-pvd-evidence-batch",
                "attemptNumber": 1,
            }
        },
        skill_id="pvd_evidence_node_extraction",
        parameters=parameters,
        provider_parameters=parameters,
        field="evidence_items",
        base_payload=base_payload,
        draft_schema=schema,
        runtime_config=SimpleNamespace(
            timeoutSeconds=5, adapter="huiyan_chat_completions", model="test-model"
        ),
        endpoint="https://provider.test/v1/chat/completions",
        base_headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        provider_id="openai_primary",
        max_output_chars=50_000,
    )

    assert result["successfulPaperCount"] == 1
    assert result["draft"]["evidence_items"] == [{"candidate": "paper-good"}]
    assert result["failedPapers"] == [{
        "paperIndex": 2,
        "candidateId": "paper-bad",
        "reason": "structured_output_invalid",
    }]
    assert attempts == {"paper-good": 1, "paper-bad": 1}


def test_shared_evidence_batch_localizes_sub_attempt_evidence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AttemptRepository(_ReservedAttemptRepository):
        def begin_sub_attempt(self, **_kwargs) -> ProviderHttpAttempt:
            raise ProviderAttemptEvidenceError("provider_sub_attempt_evidence_persistence_failed")

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, payload: dict) -> None:
            self.content = json.dumps(payload).encode("utf-8")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, _url: str, *, content: bytes, **_kwargs) -> Response:
            payload = json.loads(content)
            contract = json.loads(payload["messages"][1]["content"])
            candidate_id = contract["selectedParameters"]["paper_packets"][0]["candidate_id"]
            return Response({
                "choices": [{"message": {"content": json.dumps({
                    "research_records": [{"candidate": candidate_id}]
                })}}]
            })

    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", AttemptRepository)
    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_validate_structured_json", lambda value, *_args, **_kwargs: value
    )
    monkeypatch.setattr(
        durable_skill_runtime, "_billable_usage_wire_from_body", lambda *_args, **_kwargs: None
    )

    schema = {
        "type": "object",
        "required": ["research_records"],
        "properties": {
            "research_records": {"type": "array", "maxItems": 8, "items": {"type": "object"}}
        },
        "additionalProperties": False,
    }
    parameters = {
        "task_id": "task-evidence-reservation-failure",
        "paper_packets": [{"candidate_id": "paper-good"}, {"candidate_id": "paper-unavailable"}],
        "evidence_schema": {"schema_id": "schema-1"},
    }
    base_payload = {
        "messages": [
            {"role": "system", "content": "structured evidence"},
            {"role": "user", "content": json.dumps({
                "selectedParameters": parameters,
                "outputContract": {"frozenOutputSchema": schema},
            })},
        ]
    }

    result = durable_skill_runtime._execute_evidence_batch_requests(
        request={
            "providerEvidence": {
                "capabilityCallId": "call-evidence-reservation-failure",
                "attemptNumber": 1,
            }
        },
        skill_id="evidence_node_extraction",
        parameters=parameters,
        provider_parameters=parameters,
        field="research_records",
        base_payload=base_payload,
        draft_schema=schema,
        runtime_config=SimpleNamespace(
            timeoutSeconds=5, adapter="huiyan_chat_completions", model="test-model"
        ),
        endpoint="https://provider.test/v1/chat/completions",
        base_headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
        provider_id="openai_primary",
        max_output_chars=50_000,
    )

    assert result["successfulPaperCount"] == 1
    assert result["draft"]["research_records"] == [{"candidate": "paper-good"}]
    assert result["failedPapers"] == [{
        "paperIndex": 2,
        "candidateId": "paper-unavailable",
        "reason": "provider_attempt_evidence_unavailable",
    }]


def _evidence_skill_for_limit_decoupling() -> dict:
    input_schema = {
        "type": "object",
        "required": ["paper_packets"],
        "properties": {
            "paper_packets": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "object"},
            }
        },
        "additionalProperties": True,
    }
    draft_schema = {
        "title": "EvidenceDraft",
        "type": "object",
        "required": ["research_records"],
        "properties": {
            "research_records": {
                "type": "array",
                "maxItems": 32,
                "items": {"type": "object"},
            }
        },
        "additionalProperties": False,
    }
    output_schema = {
        "title": "EvidenceFinal",
        "type": "object",
        "required": ["research_records"],
        "properties": {
            "research_records": {
                "type": "array",
                "items": {"type": "object"},
            }
        },
        "additionalProperties": False,
    }
    return {
        "skillId": "evidence_node_extraction",
        "displayName": "Evidence Node Extraction",
        "skillPath": "skills/opentrajectory/evidence-node-extraction-skill",
        "sourceContract": {
            "frozenSchemas": {
                "input": {
                    "sha256": _canonical_schema_sha256(input_schema),
                    "schema": input_schema,
                },
                "output": {
                    "sha256": _canonical_schema_sha256(output_schema),
                    "schema": output_schema,
                },
            },
            "frozenFinalization": {
                "contractVersion": 1,
                "entrypoint": {"module": "scripts.finalize", "callable": "finalize"},
                "codeSha256": "b" * 64,
                "draftSchema": {
                    "path": "draft.schema.json",
                    "sha256": _canonical_schema_sha256(draft_schema),
                    "schema": draft_schema,
                },
            },
        },
    }


def test_synthetic_evidence_aggregate_over_max_output_chars_still_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known-bad 1: merged drafts may exceed maxOutputChars; that is not Provider failure."""
    _assume_schema_capability_for_downstream_test(monkeypatch)
    skill = _evidence_skill_for_limit_decoupling()
    max_output_chars = 80
    large_records = [
        {"candidate": f"paper-{index}", "payload": "x" * 40}
        for index in range(6)
    ]
    aggregate = {"research_records": large_records}
    serialized = json.dumps(aggregate, ensure_ascii=False, separators=(",", ":"))
    assert len(serialized) > max_output_chars

    finalize_calls: list[dict] = []

    def finalize(**kwargs):
        finalize_calls.append(kwargs)
        return skill_finalization_runtime.FinalizedSkillOutput(
            output={"research_records": large_records},
            status="succeeded",
        )

    monkeypatch.setattr(
        durable_skill_runtime,
        "_execute_evidence_batch_requests",
        lambda **_kwargs: {
            "draft": aggregate,
            "successfulPaperCount": 6,
            "failedPapers": [],
            "usageWire": None,
            "maxParallel": 2,
        },
    )
    monkeypatch.setattr(
        durable_skill_runtime, "resolve_registered_durable_skill", lambda _skill_id: skill
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: {
            **skill,
            "runtimeKind": "llm_reasoning",
            "adapterId": "huiyan_chat_completions",
            "modelProfile": "terra",
        },
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_read_skill_contract",
        lambda _skill: (
            {"output_file": "result.json", "llm": {"max_completion_tokens": 1000}},
            "Extract evidence.",
        ),
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_validate_structured_json",
        lambda value, *_args, **_kwargs: value,
    )
    monkeypatch.setattr(
        skill_finalization_runtime,
        "finalize_and_validate_registered_output",
        finalize,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "load_provider_registry",
        lambda: SimpleNamespace(
            defaultProviderId="openai_primary",
            require=lambda _provider_id: SimpleNamespace(
                providerId="openai_primary",
                adapter="huiyan_chat_completions",
                maxOutputChars=max_output_chars,
            ),
        ),
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "resolve_provider_runtime_config",
        lambda *_args, **_kwargs: SimpleNamespace(
            adapter="huiyan_chat_completions",
            model="frozen-terra-model",
            baseUrl="https://provider.test",
            apiKey="test-key",
            timeoutSeconds=30,
        ),
    )
    monkeypatch.setattr(durable_skill_runtime, "_record_durable_skill_usage", lambda **_kwargs: None)
    monkeypatch.setattr(durable_skill_runtime, "_billable_usage_wire_from_body", lambda *_a, **_k: None)

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "evidence_node_extraction",
        "parameters": {
            "paper_packets": [{"candidate_id": f"paper-{i}"} for i in range(6)],
        },
        "frozenGoal": "Extract evidence without clearing valid aggregate drafts.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
        "providerEvidence": {
            "capabilityCallId": "call-aggregate-limit",
            "attemptNumber": 1,
        },
    })

    assert result["state"] == "succeeded", result
    assert result.get("failedStage") != "output_limit_exceeded"
    assert result.get("firstMismatch") != "max_output_chars"
    assert len(finalize_calls) == 1
    assert finalize_calls[0]["draft_payload"] == aggregate
    content = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    assert content["research_records"] == large_records


def test_structured_output_parser_extracts_one_lossless_object_from_wrapper() -> None:
    expected = {"scope": "GraphRAG", "criteria": [{"criterion": "peer reviewed"}]}
    raw = f"Here is the requested JSON:\n```json\n{json.dumps(expected)}\n```\nNo additional data was added."

    assert durable_skill_runtime._parse_structured_output(raw) == expected


def test_structured_output_parser_rejects_ambiguous_multiple_objects() -> None:
    with pytest.raises(json.JSONDecodeError):
        durable_skill_runtime._parse_structured_output('first {"a":1} second {"b":2}')


def test_opentrajectory_structured_reasoning_fails_closed_after_parse_failure_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structured Skill never turns provider prose into a fake structured artifact."""
    _assume_schema_capability_for_downstream_test(monkeypatch)
    requests: list[dict] = []
    user_query = "Find reliable evidence about lithium metal batteries"
    prose = "I returned the requested fields but failed to serialize them as JSON."

    class Response(_ResponseEvidence):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "id": "provider_structured_parse_fallback",
                "model": "frozen-terra-model",
                "choices": [{"message": {"content": prose}}],
            }

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            timeout = kwargs["timeout"]
            assert isinstance(timeout, httpx.Timeout)
            assert timeout.connect == 5.0
            assert timeout.pool == 5.0
            assert timeout.write == 10.0
            assert timeout.read is None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict, content: bytes) -> Response:
            requests.append({"url": url, "headers": headers, "json": json.loads(content)})
            return Response()

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError
        ),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")
    monkeypatch.setenv("OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS", "30")

    result = _execute_provider({
        "skillId": "structured_requirement_generation",
        "parameters": {"user_query": user_query},
        "frozenGoal": user_query,
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert len(requests) == 1
    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_response_contract_invalid"
    assert result["failedStage"] == "json_parse_failed"
    assert result["firstMismatch"] == "structured_output_json_object_required"
    assert "artifactPayload" not in result


def test_problem_framing_recoverable_guard_produces_degraded_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assume_schema_capability_for_downstream_test(monkeypatch)
    calls = 0
    user_query = "Research fast-charging batteries"
    structured = _valid_problem_framing(user_query)
    structured["constraints_criteria"]["interpreted_constraints"][0][
        "downstream_filter_rules"
    ] = ["filter: rate >= 2C", "rank_relevant_fast_charge_first"]

    class Response(_ResponseEvidence):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(structured)}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError
        ),
        raising=False,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "problem_framing_generation",
        "parameters": {"user_query": user_query},
        "frozenGoal": user_query,
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert calls == 1
    assert result["state"] == "succeeded"
    assert "formalGate" not in result
    decoded = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    constraint = decoded["constraints_criteria"]["interpreted_constraints"][0]
    assert "downstream_filter_rules" not in constraint
    assert constraint["downstream_search_terms"] == ["fast charging", "C-rate"]
    metadata = result["artifactPayload"]["presentation"]["metadata"]
    assert metadata["artifactQuality"] == "degraded"
    assert metadata["resultProtocol"]["status"] == "degraded"
    assert metadata["resultProtocol"]["warnings"] == metadata["warnings"]
    assert (
        "deprecated filter-rule field was ignored without inspecting its content"
    ) in metadata["warnings"]


def test_problem_framing_fatal_query_identity_drift_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assume_schema_capability_for_downstream_test(monkeypatch)
    calls = 0
    user_query = "Research fast-charging batteries"
    structured = _valid_problem_framing(user_query)
    structured["intent_understanding"]["original_user_query"] = (
        "A different research request"
    )

    class Response(_ResponseEvidence):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(structured)}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError
        ),
        raising=False,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "problem_framing_generation",
        "parameters": {"user_query": user_query},
        "frozenGoal": user_query,
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert calls == 1
    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_response_contract_invalid"
    assert result["failedStage"] == "runtime_guard_failed"
    assert result["firstMismatch"] == "PF-R02"
    assert "artifactPayload" not in result


def test_problem_framing_legacy_filter_has_no_authority_over_explicit_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assume_schema_capability_for_downstream_test(monkeypatch)
    calls = 0
    user_query = "Research batteries charged at exactly 2C"
    structured = _valid_problem_framing(user_query)
    structured["intent_understanding"]["boundary_conditions"] = ["exactly 2C"]
    structured["intent_understanding"]["ambiguities"] = []
    constraint = structured["constraints_criteria"]["interpreted_constraints"][0]
    constraint.update({
        "constraint_name": "charge_rate",
        "original_phrase": "exactly 2C",
        "constraint_type": "condition",
        "interpreted_value": "2C",
        "candidate_values": [],
        "semantic_state": "ambiguous",
        "domain_rationale": "The model incorrectly requested confirmation.",
        "downstream_search_terms": ["2C charge rate"],
        "downstream_filter_rules": ["filter: rate == 2C"],
        "evidence_basis": "exactly 2C",
    })

    class Response(_ResponseEvidence):
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(structured)}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError
        ),
        raising=False,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_billable_usage_wire_from_body",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "problem_framing_generation",
        "parameters": {"user_query": user_query},
        "frozenGoal": user_query,
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert calls == 1
    assert result["state"] == "succeeded"
    decoded = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]))
    constraint = decoded["constraints_criteria"]["interpreted_constraints"][0]
    assert "downstream_filter_rules" not in constraint
    assert constraint["original_phrase"] == "exactly 2C"
    assert constraint["interpreted_value"] == "2C"
    metadata = result["artifactPayload"]["presentation"]["metadata"]
    assert metadata["artifactQuality"] == "degraded"
    assert (
        "deprecated filter-rule field was ignored without inspecting its content"
    ) in metadata["warnings"]


@pytest.mark.parametrize(
    ("content", "schema", "max_output_chars", "failed_stage"),
    [
        ("not json", {"type": "object"}, 10_000, "json_parse_failed"),
        (json.dumps(_valid_structured_requirement("diagnostic query")), {"type": "object", "required": ["missing"]}, 10_000, "json_schema_failed"),
        (json.dumps(_valid_structured_requirement("diagnostic query")), {"type": "object"}, 1, "output_limit_exceeded"),
    ],
)
def test_opentrajectory_structured_contract_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    schema: dict,
    max_output_chars: int,
    failed_stage: str,
) -> None:
    _assume_schema_capability_for_downstream_test(monkeypatch)
    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": content}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            return Response()

    contract = {
        "contractVersion": 1,
        "guardVersion": "p06c-v1",
        "input": {"sha256": "0" * 64, "schema": {"type": "object"}},
        "output": {"sha256": "0" * 64, "schema": schema},
    }
    registry = SimpleNamespace(defaultProviderId="openai_primary", require=lambda _provider_id: SimpleNamespace(
        providerId="openai_primary", adapter="huiyan_chat_completions", maxOutputChars=max_output_chars,
    ))
    monkeypatch.setattr(durable_skill_runtime, "_structured_json_contract", lambda _skill: contract)
    monkeypatch.setattr(durable_skill_runtime, "load_provider_registry", lambda: registry)
    monkeypatch.setattr(durable_skill_runtime, "resolve_provider_runtime_config", lambda *_args, **_kwargs: SimpleNamespace(
        adapter="huiyan_chat_completions", model="frozen-terra-model", baseUrl="https://provider.test",
        apiKey="test-key", timeoutSeconds=30,
    ))
    monkeypatch.setattr(durable_skill_runtime, "httpx", SimpleNamespace(
        Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError,
    ))

    result = _execute_provider({
        "skillId": "structured_requirement_generation",
        "parameters": {"user_query": "diagnostic query"},
        "frozenGoal": "diagnostic query",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_response_contract_invalid"
    assert result["failedStage"] == failed_stage
    assert "artifactPayload" not in result

def test_unclassified_skills_fail_closed_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_constructions = 0

    class NoHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal client_constructions
            client_constructions += 1

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=NoHttpClient, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    for skill_id in (
        "database_search",
        "code_implementation",
        "experiment_execution_l3",
    ):
        result = _execute_provider({
            "skillId": skill_id,
            "parameters": {},
            "frozenGoal": "Perform the requested real action.",
            "logicalProviderId": "openai_primary",
            "modelProfile": "terra",
        })
        assert result == {
            "state": "failed",
            "summary": "This capability has no truthful durable runtime.",
            "failureClassification": "skill_runtime_unavailable",
        }

    assert client_constructions == 0


def test_registry_runtime_metadata_is_explicit_and_fail_closed() -> None:
    skills = durable_skill_runtime.list_registered_durable_skills()
    by_id = {skill["skillId"]: skill for skill in skills}

    assert len(by_id) == len(skills)
    assert by_id["intent_understanding"]["runtimeContract"]["kind"] == "llm_reasoning"
    assert by_id["materials_magnet_design_mvp"]["runtimeContract"]["kind"] == "external_adapter"
    assert by_id["candidate_tiering"]["runtimeContract"]["kind"] == "deterministic"
    assert by_id["literature_search"]["runtimeContract"] == {
        "kind": "llm_reasoning",
        "requires_external_action": False,
        "adapter_id": "huiyan_chat_completions",
        "evidence_contract_version": "none",
    }
    assert by_id["literature_search"]["durableExecutable"] is True
    for skill_id in ("database_search", "code_implementation", "experiment_execution_l3"):
        assert by_id[skill_id]["runtimeContract"] is None
        assert by_id[skill_id]["durableExecutable"] is False
    assert by_id["academic_source_retrieval_skill"]["durableExecutable"] is True
    assert by_id["candidate_tiering"]["durableExecutable"] is True
    for skill_id in (
        "structured_requirement_generation",
        "constraint_interpretation_skill",
        "retrieval_matrix_generation",
    ):
        assert by_id[skill_id]["status"] == "ready"
        assert by_id[skill_id]["durableExecutable"] is True
        assert by_id[skill_id]["sourceContract"]["structured_output"] == {
            "contract_version": 1,
            "guard_version": "p06c-v1",
        }
        assert set(by_id[skill_id]["sourceContract"]["frozenSchemas"]) == {"input", "output"}
    for skill_id in ("evidence_node_extraction", "mechanism_chain_synthesis"):
        assert by_id[skill_id]["status"] == "ready"
        assert by_id[skill_id]["durableExecutable"] is True
        assert set(by_id[skill_id]["sourceContract"]["frozenSchemas"]) == {"input", "output"}
    assert by_id["evidence_node_extraction"]["sourceContract"]["finalization"]["contract_version"] == 1
    assert by_id["mechanism_chain_synthesis"]["sourceContract"]["structured_output"] == {
        "contract_version": 1,
        "guard_version": "p06c-v1",
    }
    assert by_id["academic_source_retrieval_skill"]["runtime"] == "python_adapter"
    assert by_id["experiment_design_l3"]["runtimeContract"] is None
    assert by_id["literature_filter_l3"]["runtimeContract"] is None
    assert by_id["paper_writing_l3"]["runtimeContract"] is None

    statistics = durable_skill_runtime.durable_skill_runtime_statistics()
    assert statistics["TOTAL_SKILLS"] == len(skills)
    assert statistics["UNKNOWN_SKILLS"] == 0
    assert (
            statistics["LLM_REASONING_SKILLS"]
            + statistics["DETERMINISTIC_SKILLS"]
            + statistics["EXTERNAL_ADAPTER_SKILLS"]
            + statistics["SKILL_SCRIPT_SKILLS"]
            + statistics["FAIL_CLOSED_SKILLS"]
        + statistics["UNKNOWN_SKILLS"]
        == statistics["TOTAL_SKILLS"]
    )


@pytest.mark.parametrize(
    "raw_contract",
    [
        None,
        {},
        {
            "kind": "unknown",
            "requires_external_action": False,
            "adapter_id": "huiyan_chat_completions",
            "evidence_contract_version": "none",
        },
        {
            "kind": "llm_reasoning",
            "requires_external_action": True,
            "adapter_id": "huiyan_chat_completions",
            "evidence_contract_version": "none",
        },
    ],
)
def test_missing_or_unknown_runtime_contract_fails_closed(raw_contract: object) -> None:
    source_contract = {} if raw_contract is None else {"runtime_contract": raw_contract}
    assert durable_skill_runtime._runtime_contract({"sourceContract": source_contract}) is None


def test_explicit_reasoning_skill_uses_generated_contract_without_external_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []
    content = """# Result

## Findings

The supplied design calls for a bounded comparison.

## Evidence

Only the frozen request inputs were used.

## Next Steps

Review the comparison plan before execution.
"""

    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({
                "title": "Ablation plan",
                "content": content,
            })}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict, content: bytes) -> Response:
            requests.append(json.loads(content))
            return Response()

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "ablation_planning",
        "parameters": {"comparison": "remove one component at a time"},
        "frozenGoal": "Plan a bounded comparison.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert result["state"] == "succeeded"
    assert result["externalActionEvidence"] == []
    assert len(requests) == 1
    contract = json.loads(requests[0]["messages"][1]["content"])
    assert contract["outputContract"]["contractSource"] == "generated_registered_skill"
    assert contract["runtimeContract"] == {
        "kind": "llm_reasoning",
        "requiresExternalAction": False,
        "adapterId": "huiyan_chat_completions",
        "evidenceContractVersion": "none",
    }
    assert "externalActionEvidence" not in contract
    assert result["artifactPayload"]["presentation"]["metadata"]["runtime"] == "llm_reasoning"


def test_reasoning_skill_rejects_incoming_external_action_evidence_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_constructions = 0

    class NoHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal client_constructions
            client_constructions += 1

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=NoHttpClient, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "ablation_planning",
        "parameters": {},
        "frozenGoal": "Bounded comparison.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
        "externalActionEvidence": [{"adapter": "search"}],
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_configuration"
    assert client_constructions == 0


def test_external_adapter_success_has_structured_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: {
            "state": "succeeded",
            "iterations": [{
                "mcp_calls": [{
                    "mcp_name": "diffcsp-generate",
                    "tool": "generate_structures",
                    "request_sha256": "a" * 64,
                    "transport_outcome": "ok",
                }]
            }],
            "sources": [{"doi": "10.1000/verified"}],
            "count": 1,
        },
    )

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "materials_magnet_design_mvp",
        "parameters": {"query": "bounded materials candidates"},
    })

    assert result["state"] == "succeeded"
    content = base64.b64decode(result["artifactPayload"]["contentBase64"])
    digest = hashlib.sha256(content).hexdigest()
    assert result["externalActionEvidence"] == [{
        "adapter": "python_adapter:materials_magnet_design_mvp",
        "outcome": "completed",
        "evidenceRef": f"skill-result:materials_magnet_design_mvp:sha256:{digest}",
        "contentSha256": digest,
    }]
    assert result["artifactPayload"]["presentation"]["metadata"]["runtime"] == "external_adapter"


def test_external_adapter_without_action_evidence_cannot_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: {"state": "succeeded", "iterations": [], "top_k_table": "none"},
    )

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "materials_magnet_design_mvp",
        "parameters": {},
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "external_action_evidence_missing"


def test_external_adapter_exception_is_unknown_after_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_once(**_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("adapter interrupted")

    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", fail_once)

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "materials_magnet_design_mvp",
        "parameters": {},
    })

    assert result["state"] == "unknown"
    assert result["externalResultUnknown"] is True
    assert calls == 1


def test_declared_source_identifier_values_reject_placeholders() -> None:
    assert durable_skill_runtime._valid_source_identifier("待补 DOI") is False
    assert durable_skill_runtime._valid_source_identifier("未知链接") is False


def test_external_adapter_partial_failure_stays_usable_with_partial_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: {
            "state": "partial_failure",
            "partial_failure": ["LLM crossover HTTP failed"],
            "iterations": [{
                "mcp_calls": [{
                    "mcp_name": "diffcsp-generate",
                    "tool": "generate_structures",
                    "request_sha256": "b" * 64,
                    "transport_outcome": "ok",
                }]
            }],
            "candidates": [{"formula": "Fe2B"}],
        },
    )

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "materials_magnet_design_mvp",
        "parameters": {"goal": "bounded magnet candidates"},
    })

    assert result["state"] == "succeeded"
    assert "partial" in result["summary"]
    assert result["externalActionEvidence"][0]["outcome"] == "partial"


def test_retrieval_and_reading_adapters_are_durable_executable() -> None:
    skills = {
        skill["skillId"]: skill
        for skill in durable_skill_runtime.list_registered_durable_skills()
    }
    assert skills["academic_source_retrieval_skill"]["durableExecutable"] is True
    assert skills["opentrajectory_source_acquisition"]["durableExecutable"] is True
    assert skills["deepxiv_progressive_reading"]["durableExecutable"] is True
    assert skills["candidate_tiering"]["durableExecutable"] is True
    assert skills["candidate_dedup_and_provenance"]["durableExecutable"] is True
    assert skills["literature_review"]["durableExecutable"] is True
    assert skills["literature_review"]["status"] == "ready"
    # Adapter Skills resolve without Provider secrets; reasoning Skills still need
    # a configured HUIYAN provider at live resolve time.
    assert durable_skill_runtime.resolve_registered_durable_skill_runtime(
        "academic_source_retrieval_skill"
    ) is not None
    assert durable_skill_runtime.resolve_registered_durable_skill_runtime(
        "opentrajectory_source_acquisition"
    ) is not None
    assert durable_skill_runtime.resolve_registered_durable_skill_runtime(
        "candidate_tiering"
    ) is not None
    assert durable_skill_runtime.resolve_registered_durable_skill_runtime(
        "deepxiv_progressive_reading"
    ) is not None


def test_retrieval_adapter_evidence_shape_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: {
            "metadata_candidates": [{
                "source_name": "openalex",
                "provenance": [{"source": "openalex", "query": "solid electrolyte"}],
            }],
            "retrieval_report": {
                "source_counts": {"openalex": 1, "arxiv": 0},
                "query_counts": {"solid electrolyte": 1},
                "failed_sources": [],
                "total_candidates": 1,
                "partial_success": False,
            },
        },
    )
    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "academic_source_retrieval_skill",
        "parameters": {"user_query": "solid electrolyte", "retrieval_config": {}},
    })
    assert result["state"] == "succeeded"
    assert result["externalActionEvidence"][0]["adapter"] == (
        "python_adapter:academic_source_retrieval_skill"
    )
    assert result["externalActionEvidence"][0]["outcome"] == "completed"


def test_source_acquisition_adapter_evidence_shape_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: {
            "task_id": "task-1",
            "skill_id": "opentrajectory_source_acquisition",
            "candidates": [{"candidate_id": "c1", "title": "Paper"}],
            "retrieval_report": {
                "source_counts": {"openalex": 1, "semantic_scholar": 0, "arxiv": 0},
                "query_counts": {"solid electrolyte": 1},
                "failed_sources": [],
                "total_candidates": 1,
                "partial_success": False,
            },
            "dedup_report": {"input_count": 1, "output_count": 1},
            "summary": {"total_candidates": 1},
            "warnings": [],
            "errors": [],
        },
    )
    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "opentrajectory_source_acquisition",
        "parameters": {
            "task_id": "task-1",
            "user_query": "solid electrolyte",
            "search_matrix": [],
            "expanded_query_variants": [],
        },
    })
    assert result["state"] == "succeeded"
    assert result["externalActionEvidence"][0]["adapter"] == (
        "python_adapter:opentrajectory_source_acquisition"
    )
    assert result["externalActionEvidence"][0]["outcome"] == "completed"


def test_retrieval_evidence_gate_preserves_existing_safe_report_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: {
            "metadata_candidates": [],
            "retrieval_report": {
                "source_counts": {"openalex": 0, "semantic_scholar": 0, "arxiv": 0},
                "query_counts": {},
                "failed_sources": [],
                "total_candidates": 0,
            },
        },
    )

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "academic_source_retrieval_skill",
        "parameters": {"user_query": "GraphRAG", "retrieval_config": {}},
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "external_action_evidence_missing"
    assert result["failedStage"] == "retrieval_report_evidence_gate"
    assert result["firstMismatch"] == (
        "retrieval-report-dict.sources-openalex-ok-0_semantic_scholar-ok-0_arxiv-ok-0"
        ".queries-0.final-0.provenance-0.failed-0.not-attempted"
    )


def test_retrieval_evidence_gate_accepts_attempted_zero_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: {
            "metadata_candidates": [],
            "retrieval_report": {
                "source_counts": {"openalex": 0, "semantic_scholar": 0, "arxiv": 0},
                "query_counts": {"GraphRAG": 0},
                "failed_sources": [],
                "total_candidates": 0,
                "partial_success": False,
            },
        },
    )

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "academic_source_retrieval_skill",
        "parameters": {"user_query": "GraphRAG", "retrieval_config": {}},
    })

    assert result["state"] == "succeeded"
    assert result["externalActionEvidence"][0]["outcome"] == "completed"


def test_durable_huiyan_5xx_is_a_known_provider_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict] = []

    class Response(_ResponseEvidence):
        status_code = 503
        text = "temporary upstream failure"

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "service unavailable",
                request=httpx.Request("POST", "https://provider.test/v1/chat/completions"),
                response=httpx.Response(503, request=httpx.Request("POST", "https://provider.test/v1/chat/completions")),
            )

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict, content: bytes) -> Response:
            requests.append(json.loads(content))
            return Response()

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "intent_understanding",
        "parameters": {},
        "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_http_error"
    assert result["failedStage"] == "provider_http_response"
    assert result["firstMismatch"] == "http_503"
    assert len(requests) == 1


def test_durable_huiyan_4xx_is_a_known_rejected_request(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response(_ResponseEvidence):
        status_code = 422

        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
            raise httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(422, request=request))

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            return Response()

    monkeypatch.setattr(durable_skill_runtime, "httpx", SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError), raising=False)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "intent_understanding", "parameters": {}, "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary", "modelProfile": "terra",
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_rejected_request"


def test_durable_huiyan_timeout_is_unknown_after_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(durable_skill_runtime, "httpx", SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError), raising=False)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "intent_understanding", "parameters": {}, "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary", "modelProfile": "terra",
    })

    assert result["state"] == "unknown"
    assert result["externalResultUnknown"] is True
    assert result["providerExecutionId"] == "attempt-call-durable-skill-unit"
    assert calls == 1


def test_durable_huiyan_connect_failure_is_known_not_submitted(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            raise httpx.ConnectTimeout("connect timed out")

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=Client,
            HTTPStatusError=httpx.HTTPStatusError,
            HTTPError=httpx.HTTPError,
            PoolTimeout=httpx.PoolTimeout,
            ConnectTimeout=httpx.ConnectTimeout,
            ConnectError=httpx.ConnectError,
        ),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "intent_understanding",
        "parameters": {},
        "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_connection_unavailable"
    assert result["failedStage"] == "provider_transport_pre_submission"
    assert result["diagnostics"]["providerExecutionId"] == "attempt-call-durable-skill-unit"


def test_durable_huiyan_completed_plain_text_response_materializes_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "not json"}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            return Response()

    monkeypatch.setattr(durable_skill_runtime, "httpx", SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError), raising=False)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "intent_understanding", "parameters": {}, "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary", "modelProfile": "terra",
    })

    assert result["state"] == "succeeded"
    decoded = base64.b64decode(result["artifactPayload"]["contentBase64"]).decode("utf-8")
    assert "not json" in decoded


def test_durable_huiyan_completed_non_json_body_is_known_contract_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            raise ValueError("response body is not JSON")

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs) -> Response:
            return Response()

    monkeypatch.setattr(durable_skill_runtime, "httpx", SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError), raising=False)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = _execute_provider({
        "skillId": "intent_understanding", "parameters": {}, "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary", "modelProfile": "terra",
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_response_contract_invalid"


def test_durable_huiyan_missing_frozen_provider_configuration_fails_before_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_MODEL_TERRA", raising=False)
    result = _execute_provider({
        "skillId": "intent_understanding", "parameters": {}, "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary", "modelProfile": "terra",
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_runtime_unavailable"


def test_durable_huiyan_without_provider_evidence_submits_zero_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_constructions = 0
    submissions = 0

    class NoHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal client_constructions
            client_constructions += 1

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            nonlocal submissions
            submissions += 1
            raise AssertionError("HTTP submission must not be reached")

    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(
            Client=NoHttpClient,
            HTTPStatusError=httpx.HTTPStatusError,
            HTTPError=httpx.HTTPError,
        ),
        raising=False,
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "frozen-terra-model")

    result = durable_skill_runtime.execute_durable_skill({
        "skillId": "intent_understanding",
        "parameters": {},
        "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_attempt_evidence"
    assert result["failedStage"] == "provider_attempt_evidence"
    assert result["firstMismatch"] == "provider_attempt_evidence_invalid"
    assert client_constructions == 0
    assert submissions == 0


def test_small_output_limit_is_sent_exactly_and_enforced_after_one_post(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict] = []
    registry = SimpleNamespace(defaultProviderId="openai_primary", require=lambda provider_id: SimpleNamespace(
        providerId=provider_id,
        adapter="huiyan_chat_completions",
        maxOutputChars=7,
    ))

    def resolve(provider_id: str, *, model_profile: str, registry: object) -> SimpleNamespace:
        assert provider_id == "openai_primary"
        assert model_profile == "terra"
        assert registry is registry_authority
        return SimpleNamespace(
            adapter="huiyan_chat_completions",
            model="server-authoritative-model",
            baseUrl="https://provider.test",
            apiKey="secret-key",
            timeoutSeconds=30,
        )

    registry_authority = registry

    class Response(_ResponseEvidence):
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps({
                "title": "Research configuration",
                "content": VALID_RESEARCH_CONFIG,
            })}}]}

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, headers: dict, content: bytes) -> Response:
            requests.append(json.loads(content))
            return Response()

    monkeypatch.setattr(durable_skill_runtime, "load_provider_registry", lambda: registry_authority)
    monkeypatch.setattr(durable_skill_runtime, "resolve_provider_runtime_config", resolve)
    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
    )

    result = _execute_provider({
        "skillId": "intent_understanding",
        "parameters": {},
        "frozenGoal": "Bounded research goal.",
        "logicalProviderId": "openai_primary",
        "modelProfile": "terra",
    })

    assert result["state"] == "succeeded"
    assert result["artifactPayload"]["presentation"]["contentSize"] > 0
    decoded = base64.b64decode(result["artifactPayload"]["contentBase64"]).decode("utf-8")
    assert len(decoded) <= 7
    assert "content_truncated_to_max_output_chars" in result["artifactPayload"]["presentation"]["metadata"].get("warnings", [])
    assert len(requests) == 1
    assert requests[0]["model"] == "server-authoritative-model"
    assert requests[0]["max_tokens"] == 1
    provider_contract = json.loads(requests[0]["messages"][1]["content"])
    assert provider_contract["outputContract"]["maxOutputChars"] == 7


@pytest.mark.parametrize(
    "key",
    ["provider", "model", "runtime", "secret", "worker_id", "workerId", "call_id", "owner_id"],
)
def test_internal_execution_keys_are_rejected_even_when_redacted(key: str) -> None:
    assert durable_skill_runtime._contains_internal_execution_key({"nested": {key: "[REDACTED]"}}) is True


@pytest.mark.parametrize(
    "content,forbidden_values",
    [
        (VALID_RESEARCH_CONFIG + "notes: " + ("x" * 32), []),
        (VALID_RESEARCH_CONFIG.replace("resources:\n  compute: ''\n  data_status: ''\n", ""), []),
        (VALID_RESEARCH_CONFIG.replace("target_venue:\n  primary: ''\n  tier: ''\n", "target_venue: {}\n"), []),
        (VALID_RESEARCH_CONFIG.replace("  required_metrics: []\n", ""), []),
        (VALID_RESEARCH_CONFIG.replace("project: Bounded feedback study\n", "project: first\nproject: second\n"), []),
        (VALID_RESEARCH_CONFIG.replace(
            "target_venue:\n  primary: ''\n  tier: ''\n",
            "target_venue:\n  provider: '[REDACTED]'\ntarget_venue:\n  primary: ''\n  tier: ''\n",
        ), []),
        (VALID_RESEARCH_CONFIG + "notes: run_internal_123\n", ["run_internal_123"]),
    ],
)
def test_huiyan_artifact_normalizes_output_limit_schema_and_presentation_drift(
    content: str, forbidden_values: list[str],
) -> None:
    max_output_chars = len(VALID_RESEARCH_CONFIG) + 8 if not forbidden_values else 10_000
    body = {
        "id": "provider-response",
        "choices": [{"message": {"content": json.dumps({"title": "Research configuration", "content": content})}}],
    }

    artifact = durable_skill_runtime._parse_huiyan_artifact(
        body,
        skill_id="intent_understanding",
        max_output_chars=max_output_chars,
        forbidden_values=forbidden_values,
    )
    assert artifact["content"].strip()
    assert len(artifact["content"]) <= max_output_chars


def test_production_skill_execution_rejects_unmaterialized_declared_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production dispatch cannot bypass a Binding-v2 declared input contract."""
    from app.modules.skill_binding_source import BINDING_CONTRACT_VERSION, load_skill_binding_source_rows

    binding_row = next(
        row
        for row in load_skill_binding_source_rows()
        if row["skillId"] == "retrieval_matrix_generation"
        and row["bindingId"] == "bind:opentrajectory:retrieval_strategy_design:skill:retrieval_matrix_generation"
    )
    binding_evidence = {
        "bindingId": binding_row["bindingId"],
        "bindingVersion": binding_row["bindingVersion"],
        "bindingContractVersion": BINDING_CONTRACT_VERSION,
        "inputSources": binding_row.get("inputSources"),
        "runtimeInputs": binding_row.get("runtimeInputs"),
    }
    called: list[dict] = []
    monkeypatch.setattr(
        durable_skill_runtime,
        "execute_durable_skill",
        lambda request: called.append(request) or {"state": "succeeded"},
    )

    missing = durable_skill_runtime.execute_production_durable_skill({
        "skillId": "retrieval_matrix_generation",
        "parameters": {"user_query": "x"},
    })

    assert missing["state"] == "failed"
    assert missing["failureClassification"] == "skill_binding_precondition_missing"
    assert called == []

    accepted = durable_skill_runtime.execute_production_durable_skill({
        "skillId": "retrieval_matrix_generation",
        "parameters": {"user_query": "x"},
        "bindingInputEvidence": binding_evidence,
    })

    assert accepted["state"] == "succeeded"
    assert len(called) == 1


def test_source_acquisition_progress_is_persisted_as_user_visible_research_event(monkeypatch) -> None:
    from app.execution import durable_skill_runtime as runtime_module
    from app.modules import task_events as task_events_module

    captured: list[dict] = []

    class FakeRepository:
        def append_many(self, **kwargs):
            captured.append(kwargs)
            return 1

    monkeypatch.setattr(task_events_module, "TaskResearchEventRepository", FakeRepository)
    callback = runtime_module._source_acquisition_progress_callback(
        {
            "taskId": "task-1",
            "ownerId": "owner-1",
            "workspaceId": "workspace-1",
            "executionDecisionId": "decision-1",
            "workUnitId": "workunit-1",
            "capabilityCallId": "call-1",
        }
    )
    assert callback is not None
    callback(
        {
            "phase": "source_batch_completed",
            "source_name": "openalex",
            "query_index": 0,
            "source_candidate_count": 7,
            "failed": False,
            "sample_candidates": [
                {"title": "A useful paper"},
                {"title": "Another useful paper"},
            ],
        }
    )

    assert len(captured) == 1
    call = captured[0]
    assert call["source_identity"] == "decision-1"
    event = call["events"][0]
    assert event["headline"] == "OpenAlex 已找到 7 篇候选文献"
    assert "A useful paper" in event["summary"]
    assert event["payload"]["aggregateCount"] == 7


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


def _execute_generic_policy_skill(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response_contents: list[object],
    status_codes: list[int] | None = None,
    finalize=None,
    recovery_attempts: int = 1,
) -> tuple[dict, list[dict], list[dict], list[object]]:
    _assume_schema_capability_for_downstream_test(monkeypatch)
    skill = _generic_frozen_finalizer_skill()
    runtime = {
        **skill,
        "runtimeKind": "llm_reasoning",
        "adapterId": "huiyan_chat_completions",
        "modelProfile": None,
    }
    requests: list[dict] = []
    evidence: list[dict] = []
    timeouts: list[object] = []
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
            return None

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
                return None
            request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request, content=self.content)
            raise httpx.HTTPStatusError("provider rejected request", request=request, response=response)

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            timeouts.append(kwargs["timeout"])

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
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

    monkeypatch.setattr(durable_skill_runtime, "resolve_registered_durable_skill", lambda _skill_id: skill)
    monkeypatch.setattr(
        durable_skill_runtime,
        "resolve_registered_durable_skill_runtime",
        lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "_read_skill_contract",
        lambda _skill: ({"output_file": "result.json", "llm": {"max_completion_tokens": 1000}}, "Execute."),
    )
    if finalize is None:
        finalize = lambda **_kwargs: skill_finalization_runtime.FinalizedSkillOutput(
            output={"canonical": "final output"}, status="succeeded"
        )
    monkeypatch.setattr(
        skill_finalization_runtime,
        "finalize_and_validate_registered_output",
        finalize,
    )
    monkeypatch.setattr(durable_skill_runtime, "ProviderHttpAttemptRepository", EvidenceRepository)
    monkeypatch.setattr(
        durable_skill_runtime,
        "httpx",
        SimpleNamespace(Client=Client, HTTPStatusError=httpx.HTTPStatusError, HTTPError=httpx.HTTPError),
    )
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.delenv("LLM_PROVIDER_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_MODEL_TERRA", raising=False)

    execution_policy = {
        **_frozen_execution_policy(),
        "structuredOutputRecoveryAttempts": recovery_attempts,
    }
    result = durable_skill_runtime.execute_production_durable_skill({
        "skillId": skill["skillId"],
        "parameters": {"source": "frozen input"},
        "frozenGoal": "Finalize one generic draft.",
        "logicalProviderId": "changed-live-provider-value",
        "modelProfile": "sol",
        "resolvedExecutionPolicy": execution_policy,
        "providerEvidence": {
            "capabilityCallId": "call-frozen-policy",
            "attemptNumber": 1,
        },
    })
    return result, requests, evidence, timeouts


def test_frozen_execution_policy_drives_provider_wire_and_primary_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence, timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=[json.dumps({"draft": "raw draft"})],
    )

    assert result["state"] == "succeeded"
    assert len(requests) == 1
    assert requests[0]["model"] == "frozen-policy-model"
    assert requests[0]["reasoning_effort"] == "none"
    assert requests[0]["max_tokens"] == 6000
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert len(timeouts) == 1
    assert isinstance(timeouts[0], httpx.Timeout)
    assert timeouts[0].connect == 5.0
    assert timeouts[0].pool == 5.0
    assert timeouts[0].write == 10.0
    assert timeouts[0].read is None
    assert [row["kind"] for row in evidence] == ["primary"]
    assert evidence[0]["resolved_execution_policy"] == _frozen_execution_policy()


def test_retryable_structured_output_failure_uses_exactly_one_recovery_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence, timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=["not-json", json.dumps({"draft": "corrected draft"})],
    )

    assert result["state"] == "succeeded"
    assert len(requests) == 2
    assert len(timeouts) == 2
    assert all(isinstance(timeout, httpx.Timeout) for timeout in timeouts)
    assert [timeout.read for timeout in timeouts] == [None, None]
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]
    assert [row["attemptId"] for row in evidence] == ["attempt-primary", "attempt-recovery"]
    assert evidence[0]["attemptId"] != evidence[1]["attemptId"]
    assert {row["capability_call_id"] for row in evidence} == {"call-frozen-policy"}
    assert [row["outcome"] for row in evidence] == ["response_received", "response_received"]
    assert all(row["resolved_execution_policy"] == _frozen_execution_policy() for row in evidence)
    assert requests[1]["model"] == "frozen-policy-model"
    assert requests[1]["reasoning_effort"] == "none"
    assert requests[1]["max_tokens"] == 6000


def test_structured_output_failure_with_zero_recovery_authority_submits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence, timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=["not-json"],
        recovery_attempts=0,
    )

    assert result["state"] == "succeeded"
    assert base64.b64decode(result["artifactPayload"]["contentBase64"]).decode() == "not-json"
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert protocol["quarantined_items"] == [{"path": "$", "reason": "json_parse_failed"}]
    assert len(requests) == 1
    assert len(timeouts) == 1
    assert [row["kind"] for row in evidence] == ["primary"]
    assert evidence[0]["outcome"] == "response_received"
    assert evidence[0]["resolved_execution_policy"]["structuredOutputRecoveryAttempts"] == 0


def test_structured_recovery_transport_uncertain_preserves_recovery_execution_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence, _timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=[
            "not-json",
            httpx.ReadTimeout("response channel lost after submission"),
        ],
    )

    assert result["state"] == "unknown"
    assert result["externalResultUnknown"] is True
    assert result["providerExecutionId"] == "attempt-recovery"
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


def test_structured_recovery_connect_failure_is_known_not_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    result, requests, evidence, _timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=[
            "not-json",
            httpx.ConnectError("connect failed before submission", request=request),
        ],
    )

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_connection_unavailable"
    assert result["failedStage"] == "provider_transport_pre_submission"
    assert result["diagnostics"]["providerExecutionId"] == "attempt-recovery"
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


def test_non_retryable_provider_rejection_does_not_use_structured_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence, _timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=["rejected"],
        status_codes=[400],
    )

    assert result["state"] == "failed"
    assert result["failureClassification"] == "provider_rejected_request"
    assert len(requests) == 1
    assert [row["kind"] for row in evidence] == ["primary"]


def test_json_recovery_exhaustion_uses_recoverable_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, requests, evidence, _timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=["not-json", "still-not-json"],
    )

    assert result["state"] == "succeeded"
    assert base64.b64decode(result["artifactPayload"]["contentBase64"]).decode() == "not-json"
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert protocol["quarantined_items"] == [{"path": "$", "reason": "json_parse_failed"}]
    assert len(requests) == 2
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]


def test_finalizer_runtime_failure_does_not_trigger_model_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def finalize(**_kwargs):
        raise skill_finalization_runtime.SkillFinalizationError(
            "finalizer_execution_failed"
        )

    result, requests, evidence, _timeouts = _execute_generic_policy_skill(
        monkeypatch,
        response_contents=[json.dumps({"draft": "valid semantic draft"})],
        finalize=finalize,
    )

    assert result["state"] == "succeeded"
    assert json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"])) == {
        "draft": "valid semantic draft"
    }
    protocol = result["artifactPayload"]["presentation"]["metadata"]["resultProtocol"]
    assert protocol["status"] == "degraded"
    assert protocol["quarantined_items"] == [
        {"path": "$", "reason": "skill_finalization_failed"}
    ]
    assert any("finalizer_execution_failed" in warning for warning in protocol["warnings"])
    assert len(requests) == 1
    assert [row["kind"] for row in evidence] == ["primary"]

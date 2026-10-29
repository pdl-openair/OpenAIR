from __future__ import annotations

import os
import hashlib
import base64
import json

import pytest

from app.execution.durable_orchestrator_provider import execute_durable_orchestration
from app.execution.durable_orchestrator_runtime import FrozenOrchestrationExecutionRequest
from app.execution.openai_responses_provider import ProviderExecutionError


def _request(snapshot: dict | None = None) -> FrozenOrchestrationExecutionRequest:
    review = bool(isinstance(snapshot, dict) and isinstance(snapshot.get("activeWorkUnit"), dict) and snapshot["activeWorkUnit"].get("phase") == "review_artifact")
    contract = "independent_result_review_v1" if review else "test_artifact_v1"
    fields = ["verdict", "basis", "issues", "missingInputs"] if review else ["result"]
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "contractId": contract,
        "semanticVersion": "1.0.0", "type": "object", "additionalProperties": False,
        "required": fields, "properties": {field: {} for field in fields},
    }
    digest = hashlib.sha256(json.dumps(schema, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    plan = {
        "context": {"runId": "run_test", "taskId": "task_test", "ownerId": "owner_test", "workspaceId": "workspace_test"},
        "selectedProvider": "openai_primary", "selectedAgentId": "agent_test", "selectedSkillIds": [],
        "providerPolicy": {"allowedProviders": ["openai_primary"]},
        "artifactContract": contract, "artifactContractSchemaVersion": 1,
        "artifactRequiredFields": fields,
        "artifactSemanticVersion": "1.0.0", "artifactSchemaDigest": digest,
        "artifactSchema": schema, "artifactSchemaRef": "tests/test-schema.json",
    }
    return FrozenOrchestrationExecutionRequest(
        runId="run_test", taskId="task_test", ownerId="owner_test", workspaceId="workspace_test",
        orchestrationPlan=plan, frozenInputSnapshot=snapshot or {"taskId": "task_test"}, selectedAgentId="agent_test",
        selectedSkillIds=(), logicalProviderId="openai_primary", modelProfile="terra",
        artifactContract=contract, artifactContractSchemaVersion=1, artifactRequiredFields=tuple(fields),
        artifactSemanticVersion="1.0.0", artifactSchemaDigest=digest, artifactSchema=schema,
        artifactSchemaRef="tests/test-schema.json",
    )


@pytest.mark.parametrize("environment", ("development", "production", "Test", ""))
def test_durable_fake_transport_is_unreachable_outside_exact_test_environment(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setenv("OPENAIR_ENV", environment)
    monkeypatch.setenv("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT", "1")

    with pytest.raises(ProviderExecutionError, match="unavailable"):
        execute_durable_orchestration(_request())


def test_durable_fake_transport_requires_explicit_switch_and_returns_memory_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_ENV", "test")
    monkeypatch.delenv("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT", raising=False)
    with pytest.raises(ProviderExecutionError, match="unavailable"):
        execute_durable_orchestration(_request())

    monkeypatch.setenv("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT", "1")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "isolated-test-model")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "http://provider.invalid")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-only-not-a-live-key")
    monkeypatch.setenv("OPENAIR_DURABLE_ORCHESTRATE_TIMEOUT_SECONDS", "30")
    result = execute_durable_orchestration(_request())

    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0]["contentBase64"]
    content = b'{"result":{"status":"isolated"}}'
    assert result["artifacts"][0]["presentation"] == {
        "title": "Isolated durable test result",
        "mimeType": "text/plain",
        "metadata": {
            "providerId": "openai_primary",
            "adapter": "huiyan_chat_completions",
            "contentLength": len(content),
        },
        "contentSha256": hashlib.sha256(content).hexdigest(),
        "contentSize": len(content),
    }


def test_durable_fake_transport_returns_structured_usable_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAIR_ENV", "test")
    monkeypatch.setenv("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT", "1")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "isolated-test-model")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "http://provider.invalid")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-only-not-a-live-key")
    result = execute_durable_orchestration(
        _request({"activeWorkUnit": {
            "phase": "review_artifact",
            "reviewMaterials": [{
                "artifactId": "artifact_test",
                "title": "Isolated artifact",
                "mimeType": "text/plain",
                "content": "A complete deterministic research artifact body.",
                "truncated": False,
                "contentSha256": "sha256-test",
            }],
        }}),
    )
    content = base64.b64decode(result["artifacts"][0]["contentBase64"]).decode("utf-8")

    assert json.loads(content) == {
        "verdict": "usable",
        "basis": "Deterministic test review accepted the isolated artifact.",
        "issues": [],
        "missingInputs": [],
    }


def test_orchestration_meters_provider_usage_when_owner_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The openai_responses orchestration turn meters usage against the owner (U-04).

    ``_artifact_result`` drops ``usage`` from the returned dict, so
    ``execute_prepared_durable_orchestration`` must account for the tokens before
    that boundary or the call escapes the unified quota ledger.
    """
    monkeypatch.setenv("OPENAIR_ENV", "test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "isolated-test-model")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "http://provider.invalid")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-only-not-a-live-key")

    usage_wire = {"inputTokens": 10, "outputTokens": 20, "totalTokens": 30}

    class _UsageTransport:
        is_fake_transport = True

        def execute(self, request, runtime_config):  # noqa: ANN001
            return (
                {
                    "title": "Isolated durable test result",
                    "mimeType": "text/plain",
                    "content": '{"result":{"status":"isolated"}}',
                },
                usage_wire,
            )

    import app.modules.token_ledger_repository as ledger
    import app.execution.durable_orchestrator_provider as provider
    from app.execution.openai_responses_provider import OpenAIResponsesProviderAdapter

    monkeypatch.setattr(
        provider,
        "_adapter",
        lambda: OpenAIResponsesProviderAdapter(transport=_UsageTransport()),
    )
    recorded: list[dict] = []
    monkeypatch.setattr(ledger, "meter_call", lambda **kw: recorded.append(kw))

    provider.execute_prepared_durable_orchestration(
        provider.prepare_durable_orchestration(_request()),
        owner_id="owner_test",
        workspace_id="workspace_test",
        task_id="task_test",
    )

    assert len(recorded) == 1
    call = recorded[0]
    assert call["user_id"] == "owner_test"
    assert call["source"] == "durable_orchestration"
    assert call["usage_wire"] == usage_wire
    assert call["provider"] == "openai_primary"
    assert call["model"] == "isolated-test-model"
    assert call["workspace_id"] == "workspace_test"
    assert call["task_id"] == "task_test"
    assert call["status"] == "success"


def test_orchestration_skips_metering_without_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test-compat entry passes no owner; metering must fail-open (no meter_call)."""
    monkeypatch.setenv("OPENAIR_ENV", "test")
    monkeypatch.setenv("OPENAIR_DURABLE_TEST_FAKE_TRANSPORT", "1")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "isolated-test-model")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "http://provider.invalid")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-only-not-a-live-key")

    import app.modules.token_ledger_repository as ledger

    monkeypatch.setattr(
        ledger, "meter_call", lambda **kw: pytest.fail("meter_call must not run without an owner")
    )

    # execute_durable_orchestration forwards no owner_id -> _record_orchestration_usage returns early.
    execute_durable_orchestration(_request())

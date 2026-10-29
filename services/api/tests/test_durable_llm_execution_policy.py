from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.execution.durable_llm_execution_policy import (
    normalize_durable_llm_execution_policy_declaration,
    resolve_durable_llm_execution_policy,
)
from app.models.research_orchestration import WorkUnit
from app.modules import research_execution_decision_service as decision_service
from app.modules.provider_registry import build_provider_registry


_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _declaration(**overrides):
    value = {
        "contract_version": 1,
        "model_profile": "terra",
        "reasoning_effort": "none",
        "max_output_tokens": 6000,
        "read_timeout_seconds": 60,
        "response_mode": "json_object",
        "load_references": False,
        "structured_output_recovery_attempts": 1,
    }
    value.update(overrides)
    return value


def _registry():
    return build_provider_registry({
        "schemaVersion": 1,
        "defaultProviderId": "openai_primary",
        "providers": [
            {
                "providerId": "openai_primary",
                "adapter": "huiyan_chat_completions",
                "maxOutputChars": 50000,
                "environment": {
                    "model": "LLM_PROVIDER_MODEL",
                    "baseUrl": "LLM_PROVIDER_BASE_URL",
                    "apiKey": "LLM_PROVIDER_API_KEY",
                },
                "submissionSafety": {
                    "acceptsIdempotencyKey": False,
                    "returnsReceiptOrRemoteRunId": False,
                    "queryByIdempotencyKey": False,
                    "queryByReceiptOrRemoteRunId": False,
                    "submissionMode": "single_attempt_no_replay",
                },
            }
        ],
    })


def test_capability_policy_resolves_model_profile_to_immutable_workunit_values(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "validated-terra-model")
    definition = {
        "sourceContract": {"durable_llm_execution_policy": _declaration()}
    }

    resolved = resolve_durable_llm_execution_policy(definition, registry=_registry())

    assert resolved == {
        "contractVersion": 1,
        "providerId": "openai_primary",
        "adapterId": "huiyan_chat_completions",
        "model": "validated-terra-model",
        "modelProfile": "terra",
        "reasoningEffort": "none",
        "maxOutputTokens": 6000,
        "readTimeoutSeconds": 60.0,
        "responseMode": "json_object",
        "loadReferences": False,
        "structuredOutputRecoveryAttempts": 1,
    }


def test_policy_can_inherit_the_registered_durable_model_profile(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "validated-terra-model")
    declaration = _declaration()
    declaration.pop("model_profile")
    definition = {
        "sourceContract": {
            "durable_model_profile": "terra",
            "durable_llm_execution_policy": declaration,
        }
    }

    resolved = resolve_durable_llm_execution_policy(definition, registry=_registry())

    assert resolved is not None
    assert resolved["model"] == "validated-terra-model"
    assert resolved["modelProfile"] == "terra"
    assert resolved["reasoningEffort"] == "none"
    assert resolved["readTimeoutSeconds"] == 60.0


def test_policy_rejects_a_second_model_authority_when_registered_profile_exists(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "validated-terra-model")
    definition = {
        "sourceContract": {
            "durable_model_profile": "terra",
            "durable_llm_execution_policy": _declaration(),
        }
    }

    try:
        resolve_durable_llm_execution_policy(definition, registry=_registry())
    except ValueError as exc:
        assert str(exc) == "durable_llm_execution_policy_model_authority_ambiguous"
    else:
        raise AssertionError("duplicate model authority must be rejected")


def test_policy_requires_some_model_authority_at_resolution(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    declaration = _declaration()
    declaration.pop("model_profile")
    definition = {"sourceContract": {"durable_llm_execution_policy": declaration}}

    try:
        resolve_durable_llm_execution_policy(definition, registry=_registry())
    except ValueError as exc:
        assert str(exc) == "durable_llm_execution_policy_model_authority_missing"
    else:
        raise AssertionError("missing model authority must be rejected")


def test_explicit_model_is_supported_without_a_model_profile_environment(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.delenv("LLM_PROVIDER_MODEL", raising=False)
    declaration = _declaration(model_profile=None, model="validated-explicit-model")
    definition = {"sourceContract": {"durable_llm_execution_policy": declaration}}

    resolved = resolve_durable_llm_execution_policy(definition, registry=_registry())

    assert resolved is not None
    assert resolved["model"] == "validated-explicit-model"
    assert resolved["modelProfile"] is None


def test_decision_preparation_resolves_declared_policy_before_workunit_persistence(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "validated-terra-model")
    snapshot = {
        "definitions": {
            "skills": [
                {
                    "skillId": "generic_policy_skill",
                    "definitionVersion": 1,
                    "sourceContract": {"durable_llm_execution_policy": _declaration()},
                }
            ]
        }
    }
    assignment = SimpleNamespace(
        executor_ref=SimpleNamespace(
            executor_type="skill",
            executor_id="generic_policy_skill",
            definition_version=1,
        )
    )

    resolved = decision_service._resolved_execution_policy_for_assignment(snapshot, assignment)
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))["researchTask"]["workUnits"][0]
    persisted = dict(fixture)
    persisted["resolvedExecutionPolicy"] = resolved
    work_unit = WorkUnit.model_validate(persisted)

    assert work_unit.resolved_execution_policy == resolved
    assert work_unit.model_dump(mode="json", by_alias=True)["resolvedExecutionPolicy"] == resolved



def test_capability_policy_freezes_optional_total_task_budget(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://provider.test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL_TERRA", "validated-terra-model")
    declaration = _declaration(task_budget_seconds=145)
    declaration.pop("model_profile")
    definition = {
        "sourceContract": {
            "durable_model_profile": "terra",
            "durable_llm_execution_policy": declaration,
        }
    }

    resolved = resolve_durable_llm_execution_policy(definition, registry=_registry())

    assert resolved is not None
    assert resolved["readTimeoutSeconds"] == 60.0
    assert resolved["taskBudgetSeconds"] == 145.0


def test_soft_task_budget_is_independent_from_transport_observation_guard() -> None:
    declaration = _declaration(read_timeout_seconds=120, task_budget_seconds=60)
    normalized = normalize_durable_llm_execution_policy_declaration(declaration)

    assert normalized["read_timeout_seconds"] == 120.0
    assert normalized["task_budget_seconds"] == 60.0


def test_policy_declaration_rejects_more_than_one_recovery_attempt() -> None:
    declaration = _declaration(structured_output_recovery_attempts=2)
    try:
        normalize_durable_llm_execution_policy_declaration(declaration)
    except ValueError as exc:
        assert str(exc) == "durable_llm_execution_policy_recovery_budget_invalid"
    else:
        raise AssertionError("unbounded structured-output recovery must be rejected")

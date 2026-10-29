"""Canonical declaration and WorkUnit freeze contract for durable LLM execution.

The capability declaration is Registry-owned.  A resolved policy is produced
before dispatch and persisted on the WorkUnit; Provider adapters consume only
that resolved value for the execution knobs covered by this contract.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

from app.modules.provider_registry import (
    ProviderRegistry,
    ProviderRegistryError,
    load_provider_registry,
    resolve_provider_runtime_config,
)


DECLARATION_KEY = "durable_llm_execution_policy"
_CONTRACT_VERSION = 1
_ALLOWED_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})
_ALLOWED_RESPONSE_MODES = frozenset({"json_object"})
_DECLARATION_FIELDS = frozenset({
    "contract_version",
    "provider_id",
    "model",
    "model_profile",
    "reasoning_effort",
    "max_output_tokens",
    "read_timeout_seconds",
    "task_budget_seconds",
    "response_mode",
    "load_references",
    "structured_output_recovery_attempts",
})
_RESOLVED_FIELDS = frozenset({
    "contractVersion",
    "providerId",
    "adapterId",
    "model",
    "modelProfile",
    "reasoningEffort",
    "maxOutputTokens",
    "readTimeoutSeconds",
    "taskBudgetSeconds",
    "responseMode",
    "loadReferences",
    "structuredOutputRecoveryAttempts",
})


def _non_empty_string(value: Any, *, field: str, limit: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}_invalid")
    text = value.strip()
    if not text or len(text) > limit:
        raise ValueError(f"{field}_invalid")
    return text


def normalize_durable_llm_execution_policy_declaration(raw: Any) -> dict[str, Any]:
    """Validate one capability-owned execution-policy declaration.

    Every execution knob is explicit.  Values in this contract are not global
    defaults and therefore cannot be silently filled from mutable runtime
    configuration after a WorkUnit has been frozen.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("durable_llm_execution_policy_invalid")
    if set(raw) - _DECLARATION_FIELDS:
        raise ValueError("durable_llm_execution_policy_unknown_field")
    required = {
        "contract_version",
        "reasoning_effort",
        "max_output_tokens",
        "read_timeout_seconds",
        "response_mode",
        "load_references",
        "structured_output_recovery_attempts",
    }
    if not required.issubset(raw):
        raise ValueError("durable_llm_execution_policy_missing_field")
    if raw.get("contract_version") != _CONTRACT_VERSION:
        raise ValueError("durable_llm_execution_policy_version_invalid")

    model = raw.get("model")
    model_profile = raw.get("model_profile")
    if model is not None and model_profile is not None:
        raise ValueError("durable_llm_execution_policy_model_authority_invalid")
    normalized_model = (
        _non_empty_string(model, field="model") if model is not None else None
    )
    normalized_profile = (
        _non_empty_string(model_profile, field="model_profile", limit=80)
        if model_profile is not None
        else None
    )

    reasoning_effort = _non_empty_string(
        raw.get("reasoning_effort"), field="reasoning_effort", limit=32
    )
    if reasoning_effort not in _ALLOWED_REASONING_EFFORTS:
        raise ValueError("durable_llm_execution_policy_reasoning_effort_invalid")

    max_output_tokens = raw.get("max_output_tokens")
    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 1 <= max_output_tokens <= 200_000
    ):
        raise ValueError("durable_llm_execution_policy_max_output_tokens_invalid")

    read_timeout_seconds = raw.get("read_timeout_seconds")
    if (
        isinstance(read_timeout_seconds, bool)
        or not isinstance(read_timeout_seconds, (int, float))
        or not isfinite(float(read_timeout_seconds))
        or not 0 < float(read_timeout_seconds)
    ):
        raise ValueError("durable_llm_execution_policy_read_timeout_invalid")

    task_budget_seconds = raw.get("task_budget_seconds")
    if task_budget_seconds is not None:
        if (
            isinstance(task_budget_seconds, bool)
            or not isinstance(task_budget_seconds, (int, float))
            or not isfinite(float(task_budget_seconds))
            or not 0 < float(task_budget_seconds)
        ):
            raise ValueError("durable_llm_execution_policy_task_budget_invalid")

    response_mode = _non_empty_string(
        raw.get("response_mode"), field="response_mode", limit=40
    )
    if response_mode not in _ALLOWED_RESPONSE_MODES:
        raise ValueError("durable_llm_execution_policy_response_mode_invalid")

    load_references = raw.get("load_references")
    if not isinstance(load_references, bool):
        raise ValueError("durable_llm_execution_policy_reference_policy_invalid")

    recovery_attempts = raw.get("structured_output_recovery_attempts")
    if (
        isinstance(recovery_attempts, bool)
        or not isinstance(recovery_attempts, int)
        or recovery_attempts not in {0, 1}
    ):
        raise ValueError("durable_llm_execution_policy_recovery_budget_invalid")

    normalized: dict[str, Any] = {
        "contract_version": _CONTRACT_VERSION,
        "model": normalized_model,
        "model_profile": normalized_profile,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "read_timeout_seconds": float(read_timeout_seconds),
        "task_budget_seconds": (
            None if task_budget_seconds is None else float(task_budget_seconds)
        ),
        "response_mode": response_mode,
        "load_references": load_references,
        "structured_output_recovery_attempts": recovery_attempts,
    }
    provider_id = raw.get("provider_id")
    if provider_id is not None:
        normalized["provider_id"] = _non_empty_string(
            provider_id, field="provider_id", limit=80
        )
    return normalized


def resolve_durable_llm_execution_policy(
    snapshot_definition: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    registry: ProviderRegistry | None = None,
) -> dict[str, Any] | None:
    """Resolve a Registry declaration into the immutable WorkUnit policy."""
    source_contract = snapshot_definition.get("sourceContract")
    if not isinstance(source_contract, Mapping):
        return None
    raw = source_contract.get(DECLARATION_KEY)
    if raw is None:
        return None
    declaration = normalize_durable_llm_execution_policy_declaration(raw)
    registered_model_profile = source_contract.get("durable_model_profile")
    if registered_model_profile is not None:
        registered_model_profile = _non_empty_string(
            registered_model_profile, field="durable_model_profile", limit=80
        )
    declared_model = declaration["model"]
    declared_model_profile = declaration["model_profile"]
    if registered_model_profile is not None and (
        declared_model is not None or declared_model_profile is not None
    ):
        raise ValueError("durable_llm_execution_policy_model_authority_ambiguous")
    effective_model_profile = declared_model_profile or registered_model_profile
    if declared_model is None and effective_model_profile is None:
        raise ValueError("durable_llm_execution_policy_model_authority_missing")

    provider_registry = registry or load_provider_registry()
    provider_id = str(
        declaration.get("provider_id") or provider_registry.defaultProviderId
    ).strip()
    provider = provider_registry.require(provider_id)
    if declared_model is not None:
        runtime = resolve_provider_runtime_config(
            provider_id,
            model=str(declared_model),
            environ=environ,
            registry=provider_registry,
        )
    else:
        runtime = resolve_provider_runtime_config(
            provider_id,
            model_profile=str(effective_model_profile),
            environ=environ,
            registry=provider_registry,
        )
    if provider.adapter != runtime.adapter:
        raise ProviderRegistryError("Provider adapter resolution is inconsistent")
    resolved = {
        "contractVersion": _CONTRACT_VERSION,
        "providerId": provider_id,
        "adapterId": runtime.adapter,
        "model": runtime.model,
        "modelProfile": effective_model_profile,
        "reasoningEffort": declaration["reasoning_effort"],
        "maxOutputTokens": declaration["max_output_tokens"],
        "readTimeoutSeconds": declaration["read_timeout_seconds"],
        "responseMode": declaration["response_mode"],
        "loadReferences": declaration["load_references"],
        "structuredOutputRecoveryAttempts": declaration[
            "structured_output_recovery_attempts"
        ],
    }
    if declaration["task_budget_seconds"] is not None:
        resolved["taskBudgetSeconds"] = declaration["task_budget_seconds"]
    return resolved


def normalize_resolved_durable_llm_execution_policy(raw: Any) -> dict[str, Any]:
    """Validate a persisted WorkUnit policy without consulting mutable config."""
    if not isinstance(raw, Mapping):
        raise ValueError("resolved_durable_llm_execution_policy_invalid")
    actual_fields = set(raw)
    allowed_fields = set(_RESOLVED_FIELDS)
    required_fields = allowed_fields - {"taskBudgetSeconds"}
    if not required_fields.issubset(actual_fields) or not actual_fields.issubset(allowed_fields):
        raise ValueError("resolved_durable_llm_execution_policy_invalid")
    if raw.get("contractVersion") != _CONTRACT_VERSION:
        raise ValueError("resolved_durable_llm_execution_policy_version_invalid")

    provider_id = _non_empty_string(raw.get("providerId"), field="provider_id", limit=80)
    adapter_id = _non_empty_string(raw.get("adapterId"), field="adapter_id", limit=120)
    model = _non_empty_string(raw.get("model"), field="model")
    model_profile = raw.get("modelProfile")
    if model_profile is not None:
        model_profile = _non_empty_string(model_profile, field="model_profile", limit=80)
    reasoning_effort = _non_empty_string(
        raw.get("reasoningEffort"), field="reasoning_effort", limit=32
    )
    if reasoning_effort not in _ALLOWED_REASONING_EFFORTS:
        raise ValueError("resolved_durable_llm_execution_policy_reasoning_effort_invalid")

    max_output_tokens = raw.get("maxOutputTokens")
    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 1 <= max_output_tokens <= 200_000
    ):
        raise ValueError("resolved_durable_llm_execution_policy_max_output_tokens_invalid")
    read_timeout_seconds = raw.get("readTimeoutSeconds")
    if (
        isinstance(read_timeout_seconds, bool)
        or not isinstance(read_timeout_seconds, (int, float))
        or not isfinite(float(read_timeout_seconds))
        or not 0 < float(read_timeout_seconds)
    ):
        raise ValueError("resolved_durable_llm_execution_policy_read_timeout_invalid")
    task_budget_seconds = raw.get("taskBudgetSeconds")
    if task_budget_seconds is not None:
        if (
            isinstance(task_budget_seconds, bool)
            or not isinstance(task_budget_seconds, (int, float))
            or not isfinite(float(task_budget_seconds))
            or not 0 < float(task_budget_seconds)
        ):
            raise ValueError("resolved_durable_llm_execution_policy_task_budget_invalid")
    response_mode = _non_empty_string(raw.get("responseMode"), field="response_mode", limit=40)
    if response_mode not in _ALLOWED_RESPONSE_MODES:
        raise ValueError("resolved_durable_llm_execution_policy_response_mode_invalid")
    load_references = raw.get("loadReferences")
    if not isinstance(load_references, bool):
        raise ValueError("resolved_durable_llm_execution_policy_reference_policy_invalid")
    recovery_attempts = raw.get("structuredOutputRecoveryAttempts")
    if (
        isinstance(recovery_attempts, bool)
        or not isinstance(recovery_attempts, int)
        or recovery_attempts not in {0, 1}
    ):
        raise ValueError("resolved_durable_llm_execution_policy_recovery_budget_invalid")

    normalized = {
        "contractVersion": _CONTRACT_VERSION,
        "providerId": provider_id,
        "adapterId": adapter_id,
        "model": model,
        "modelProfile": model_profile,
        "reasoningEffort": reasoning_effort,
        "maxOutputTokens": max_output_tokens,
        "readTimeoutSeconds": float(read_timeout_seconds),
        "responseMode": response_mode,
        "loadReferences": load_references,
        "structuredOutputRecoveryAttempts": recovery_attempts,
    }
    if task_budget_seconds is not None:
        normalized["taskBudgetSeconds"] = float(task_budget_seconds)
    return normalized

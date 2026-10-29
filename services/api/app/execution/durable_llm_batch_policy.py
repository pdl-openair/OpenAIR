"""Registry-owned bounded batching contract for durable structured LLM Skills.

This contract only controls deterministic request partitioning and result merging.
It never decides scientific relevance, priority, or content.  A capability opts in
explicitly; the normalized declaration is frozen with the Skill source contract.
"""

from __future__ import annotations

from typing import Any, Mapping


DECLARATION_KEY = "durable_llm_batch_policy"
_CONTRACT_VERSION = 1
_ALLOWED_PARTIAL_FAILURE_MODES = frozenset({"preserve_successes"})
_DECLARATION_FIELDS = frozenset({
    "contract_version",
    "input_array_field",
    "output_array_field",
    "identity_field",
    "max_items_per_batch",
    "max_parallel",
    "partial_failure_mode",
})


def _field_name(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}_invalid")
    text = value.strip()
    if not text or len(text) > 120:
        raise ValueError(f"{field}_invalid")
    return text


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field}_invalid")
    return value


def normalize_durable_llm_batch_policy_declaration(raw: Any) -> dict[str, Any]:
    """Validate one generic batch policy without inferring scientific semantics."""
    if not isinstance(raw, Mapping):
        raise ValueError("durable_llm_batch_policy_invalid")
    if set(raw) - _DECLARATION_FIELDS:
        raise ValueError("durable_llm_batch_policy_unknown_field")
    if set(raw) != _DECLARATION_FIELDS:
        raise ValueError("durable_llm_batch_policy_missing_field")
    if raw.get("contract_version") != _CONTRACT_VERSION:
        raise ValueError("durable_llm_batch_policy_version_invalid")

    partial_failure_mode = _field_name(
        raw.get("partial_failure_mode"), field="partial_failure_mode"
    )
    if partial_failure_mode not in _ALLOWED_PARTIAL_FAILURE_MODES:
        raise ValueError("durable_llm_batch_policy_partial_failure_mode_invalid")

    return {
        "contract_version": _CONTRACT_VERSION,
        "input_array_field": _field_name(
            raw.get("input_array_field"), field="input_array_field"
        ),
        "output_array_field": _field_name(
            raw.get("output_array_field"), field="output_array_field"
        ),
        "identity_field": _field_name(raw.get("identity_field"), field="identity_field"),
        "max_items_per_batch": _bounded_int(
            raw.get("max_items_per_batch"),
            field="max_items_per_batch",
            minimum=1,
            maximum=200,
        ),
        "max_parallel": _bounded_int(
            raw.get("max_parallel"), field="max_parallel", minimum=1, maximum=16
        ),
        "partial_failure_mode": partial_failure_mode,
    }

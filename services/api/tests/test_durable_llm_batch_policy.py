from __future__ import annotations

import pytest

from app.execution.durable_llm_batch_policy import (
    normalize_durable_llm_batch_policy_declaration,
)


def _policy(**overrides):
    value = {
        "contract_version": 1,
        "input_array_field": "candidates",
        "output_array_field": "judgments",
        "identity_field": "candidate_id",
        "max_items_per_batch": 30,
        "max_parallel": 4,
        "partial_failure_mode": "preserve_successes",
    }
    value.update(overrides)
    return value


def test_generic_batch_policy_is_structural_only() -> None:
    assert normalize_durable_llm_batch_policy_declaration(_policy()) == _policy()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_items_per_batch": 0}, "max_items_per_batch_invalid"),
        ({"max_parallel": 0}, "max_parallel_invalid"),
        ({"partial_failure_mode": "drop_failures"}, "durable_llm_batch_policy_partial_failure_mode_invalid"),
        ({"scientific_score": "relevance"}, "durable_llm_batch_policy_unknown_field"),
    ],
)
def test_generic_batch_policy_rejects_scientific_or_unbounded_controls(overrides, message) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        normalize_durable_llm_batch_policy_declaration(_policy(**overrides))

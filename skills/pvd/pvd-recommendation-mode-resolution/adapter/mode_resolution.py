from __future__ import annotations

from typing import Any, Mapping


def run_pvd_recommendation_mode_resolution(payload: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    facts = payload.get("scientific_state_facts")
    if not isinstance(facts, Mapping):
        raise ValueError("scientific_state_facts is required")
    history = facts.get("experiment_history")
    if not isinstance(history, list):
        raise ValueError("scientific_state_facts.experiment_history must be an array")

    has_prior_system_recommendation = any(
        isinstance(row, Mapping) and row.get("system_recommendation") is not None
        for row in history
    )
    return {"mode": "next_round" if has_prior_system_recommendation else "initial"}

from __future__ import annotations

import copy
from typing import Any


class InputPreparationError(ValueError):
    pass


def _selected_experiment(scientific_state_facts, experiment_ref):
    history = scientific_state_facts.get("experiment_history") or []
    return copy.deepcopy(next((row for row in history if row.get("experiment_ref") == experiment_ref), {}))


def _validate_cap4_authorization(comparability):
    return copy.deepcopy(comparability)




def prepare_model_input(formal_input: dict[str, Any]) -> dict[str, Any]:
    """Build the diagnosis view without guessing facts, authorization, or experiment identity."""
    experiment_ref = formal_input.get("experiment_ref")
    if not isinstance(experiment_ref, str) or not experiment_ref:
        raise InputPreparationError("experiment_ref is required")
    facts = formal_input.get("scientific_state_facts")
    if not isinstance(facts, dict):
        raise InputPreparationError("scientific_state_facts is required")

    return {
        "user_query": formal_input.get("user_query"),
        "experiment_ref": experiment_ref,
        "selected_experiment": _selected_experiment(facts, experiment_ref),
        "experiment_history": copy.deepcopy(facts.get("experiment_history", [])),
        "evidence_comparability": _validate_cap4_authorization(formal_input.get("evidence_comparability")),
        "factor_analysis": copy.deepcopy(formal_input.get("factor_analysis")),
        "recommendation": copy.deepcopy(formal_input.get("recommendation")),
        "current_project_context": copy.deepcopy(formal_input.get("current_project_context")),
    }

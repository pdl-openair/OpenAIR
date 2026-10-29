from __future__ import annotations

from typing import Any, Mapping

PUBLIC_PARAMETER_TO_INTERNAL_FEATURE = {
    "power": ("power_w", "W"),
    "n2_flow": ("n2_sccm", "sccm"),
    "ar_flow": ("ar_sccm", "sccm"),
    "deposition_time": ("time_s", "s"),
}


def _finite_number(value):
    return float(value)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    return value


def resolve_scientific_state_facts(
    scientific_state_facts: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], list[Mapping[str, Any]]]:
    """Resolve the canonical current experimental system and validate state identities.

    This is Skill-local PVD state consumption, not a compatibility mapper.  The public
    input stays the exact frozen ScientificState fact shape; this function only resolves
    its stable refs and prepares values needed by the BO implementation.
    """

    facts = _mapping(scientific_state_facts, "scientific_state_facts")
    contexts = facts.get("experimental_system_contexts")
    history = facts.get("experiment_history")
    current_ref = str(facts.get("current_experimental_system_context_ref") or "").strip()

    context_by_ref: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(contexts):
        row = _mapping(raw, f"experimental_system_contexts[{index}]")
        ref = str(row.get("experimental_system_context_ref") or "").strip()
        context = _mapping(
            row.get("experimental_system_context"),
            f"experimental_system_contexts[{index}].experimental_system_context",
        )
        context_by_ref[ref] = context


    experiment_refs: set[str] = set()
    normalized_history: list[Mapping[str, Any]] = []
    for index, raw in enumerate(history):
        row = _mapping(raw, f"experiment_history[{index}]")
        experiment_ref = str(row.get("experiment_ref") or "").strip()
        context_ref = str(row.get("experimental_system_context_ref") or "").strip()
        experiment_refs.add(experiment_ref)
        normalized_history.append(row)

    return current_ref, context_by_ref[current_ref], normalized_history


def _actual_parameters_as_internal_features(experiment_ref, actual_execution, feature_order):
    parameters = actual_execution["parameters"]
    values = {internal: float(parameters[public]["value"]) for public, (internal, _unit) in PUBLIC_PARAMETER_TO_INTERNAL_FEATURE.items()}
    return {feature: values[feature] for feature in feature_order}


def extract_real_training_observations(
    scientific_state_facts: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> tuple[Mapping[str, Any], list[dict[str, Any]]]:
    """Build BO-internal numeric observations from canonical real project facts only."""

    current_ref, current_context, history = resolve_scientific_state_facts(
        scientific_state_facts
    )
    feature_order = tuple(str(item) for item in profile["feature_order"])
    target_metric = str(profile["objective"]["target_metric"]).strip().casefold()
    project: list[dict[str, Any]] = []

    for row in history:
        if str(row.get("experimental_system_context_ref") or "") != current_ref:
            # Historical experiments from another formal experimental system remain in
            # state but are not silently mixed into the current BO training set.
            continue
        experiment_ref = str(row.get("experiment_ref") or "").strip()
        actual = row.get("actual_execution")
        if not isinstance(actual, Mapping):
            continue
        parameters = _actual_parameters_as_internal_features(
            experiment_ref, actual, feature_order
        )
        if parameters is None:
            continue

        measurements = row.get("measurements")
        if not isinstance(measurements, list):
            continue
        for measurement in measurements:
            if not isinstance(measurement, Mapping):
                continue
            if str(measurement.get("metric_key") or "").strip().casefold() != target_metric:
                continue
            objective_value = _finite_number(measurement.get("value"))
            measurement_ref = str(measurement.get("measurement_ref") or "").strip()
            project.append(
                {
                    "observation_id": f"{experiment_ref}:{measurement_ref}",
                    "parameters": parameters,
                    "objective_value": objective_value,
                }
            )

    return current_context, project

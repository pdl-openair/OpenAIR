"""Deterministic PVD ScientificState materialization.

This module owns only the PVD fact slice inside the project's canonical
ScientificState.  It preserves unrelated accepted scientific facts and never
infers scientific meaning, parses prose, or creates a second state authority.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from app.execution.structured_skill_schema_runtime import (
    find_openair_repository_root,
)
from app.modules.research_scientific_state_authority import (
    append_scientific_state_if_changed,
    existing_scientific_facts,
    prepare_scientific_state_update,
)

PVD_EXPERIMENTAL_SYSTEM_CONTEXT_MATERIALIZER = (
    "pvd:experimental-system-context:v1"
)
PVD_RESEARCH_REQUEST_MATERIALIZER = "pvd:research-request-understanding:v1"
PVD_ROUND_EXPERIMENT_RECOMMENDATION_MATERIALIZER = (
    "pvd:round-experiment-recommendation:v1"
)
_REQUIRED_FACT_KEYS = {
    "experimental_system_contexts",
    "current_experimental_system_context_ref",
    "experiment_history",
}
_ENTRY_KEYS = {
    "experimental_system_context_ref",
    "experimental_system_context",
}
_RECOMMENDATION_DECISION_KEYS = {
    "decision_status",
    "first_run_ranking_status",
    "blocked_experiments",
    "important_unknowns",
    "next_step",
    "provenance",
}
_LEGACY_RECOMMENDATION_DECISION_KEYS = {
    "decision_status",
    "important_unknowns",
    "next_step",
    "provenance",
}
_QUERY_SOURCE_RECORD_REF = re.compile(
    r"^pvd-query-source-record:sha256:[0-9a-f]{24}$"
)


def _projection_gap(reason: str) -> None:
    logging.getLogger(__name__).warning("pvd_scientific_projection_gap: %s", reason)


def _new_pvd_fact_slice() -> dict[str, Any]:
    return {
        "experimental_system_contexts": [],
        "current_experimental_system_context_ref": "",
        "experiment_history": [],
    }


def _validated_composable_pvd_facts(value: Any) -> dict[str, Any]:
    """Validate only the PVD-owned slice and preserve other canonical facts."""

    if not isinstance(value, Mapping):
        raise ValueError("pvd_scientific_state_existing_facts_invalid")
    facts = deepcopy(dict(value))
    present = _REQUIRED_FACT_KEYS.intersection(facts)
    if not present:
        facts.update(_new_pvd_fact_slice())
        return facts
    if present != _REQUIRED_FACT_KEYS:
        raise ValueError("pvd_scientific_state_existing_facts_invalid")

    contexts = facts.get("experimental_system_contexts")
    current_ref = facts.get("current_experimental_system_context_ref")
    history = facts.get("experiment_history")
    if (
        not isinstance(contexts, list)
        or not contexts
        or not isinstance(current_ref, str)
        or not current_ref.strip()
        or not isinstance(history, list)
    ):
        raise ValueError("pvd_scientific_state_existing_facts_invalid")

    refs: set[str] = set()
    canonical_contexts: list[Any] = []
    for row in contexts:
        if not isinstance(row, Mapping) or set(row) != _ENTRY_KEYS:
            raise ValueError("pvd_scientific_state_existing_facts_invalid")
        ref = row.get("experimental_system_context_ref")
        context = row.get("experimental_system_context")
        if (
            not isinstance(ref, str)
            or not ref.strip()
            or ref in refs
            or not isinstance(context, Mapping)
        ):
            raise ValueError("pvd_scientific_state_existing_facts_invalid")
        if any(existing == context for existing in canonical_contexts):
            raise ValueError("pvd_scientific_state_duplicate_context")
        refs.add(ref)
        canonical_contexts.append(context)

    if current_ref not in refs:
        raise ValueError("pvd_scientific_state_current_ref_invalid")
    decision = facts.get("current_recommendation_decision")
    if decision is not None:
        if isinstance(decision, Mapping) and set(decision) == _LEGACY_RECOMMENDATION_DECISION_KEYS:
            decision = dict(decision)
            decision["first_run_ranking_status"] = (
                "not_applicable"
                if decision.get("decision_status") == "no_recommendation"
                else "pending"
            )
            decision["blocked_experiments"] = []
            facts["current_recommendation_decision"] = decision
        provenance = (
            decision.get("provenance") if isinstance(decision, Mapping) else None
        )
        if (
            not isinstance(decision, Mapping)
            or set(decision) != _RECOMMENDATION_DECISION_KEYS
            or decision.get("decision_status")
            not in {"recommendation", "no_recommendation"}
            or decision.get("first_run_ranking_status")
            not in {"not_applicable", "pending", "ranked"}
            or not _valid_blocked_experiments(decision.get("blocked_experiments"))
            or not isinstance(decision.get("important_unknowns"), list)
            or not isinstance(decision.get("next_step"), str)
            or not isinstance(provenance, Mapping)
            or set(provenance) != {"origin", "source_ref"}
            or provenance.get("origin") != "system"
            or not isinstance(provenance.get("source_ref"), str)
            or not provenance.get("source_ref").strip()
        ):
            raise ValueError("pvd_scientific_state_existing_facts_invalid")
    return facts


def _valid_blocked_experiments(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {"experiment_id", "reason_codes"}:
            return False
        experiment_id = row.get("experiment_id")
        reason_codes = row.get("reason_codes")
        if (
            (
                experiment_id is not None
                and (not isinstance(experiment_id, str) or not experiment_id)
            )
            or not isinstance(reason_codes, list)
            or not reason_codes
            or any(not isinstance(reason, str) or not reason for reason in reason_codes)
        ):
            return False
    return True


@lru_cache(maxsize=1)
def _pvd_state_validator() -> Draft202012Validator:
    schema_path = (
        find_openair_repository_root(Path(__file__).resolve().parent)
        / 'schemas'
        / 'pvd'
        / 'pvd-scientific-state-facts-v1.schema.json'
    )
    return Draft202012Validator(json.loads(schema_path.read_text(encoding='utf-8')))


def _validate_pvd_state_facts(facts: Mapping[str, Any]) -> None:
    errors = sorted(
        _pvd_state_validator().iter_errors(dict(facts)),
        key=lambda row: tuple(str(part) for part in row.absolute_path),
    )
    if errors:
        raise ValueError('pvd_experiment_feedback_invalid')


def _mint_context_ref(*, source_artifact_id: str) -> str:
    if not source_artifact_id.strip():
        raise ValueError("pvd_scientific_state_source_identity_missing")
    # Identity belongs to ScientificState structure, not to scientific semantics.
    # The durable source Artifact remains separately traceable through sourceRefs.
    return f"pvd-experimental-system-context:{uuid4()}"


def _upsert_context(
    *,
    facts: dict[str, Any],
    context_value: Mapping[str, Any],
    source_artifact_id: str,
) -> str:
    contexts = facts["experimental_system_contexts"]
    matching_refs = [
        row["experimental_system_context_ref"]
        for row in contexts
        if row["experimental_system_context"] == context_value
    ]
    if len(matching_refs) > 1:
        raise ValueError("pvd_scientific_state_duplicate_context")
    if matching_refs:
        return matching_refs[0]

    context_ref = _mint_context_ref(source_artifact_id=source_artifact_id)
    contexts.append(
        {
            "experimental_system_context_ref": context_ref,
            "experimental_system_context": deepcopy(dict(context_value)),
        }
    )
    return context_ref


def _state_parameter_values(
    values: Any,
    *,
    source_record_ref: str,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        _projection_gap("actual_execution:not_list")
        return []
    rows: list[dict[str, Any]] = []
    for row in values:
        if not isinstance(row, Mapping):
            _projection_gap("actual_execution:row_not_object")
            continue
        parameter_key = row.get("parameter_key")
        value_type = row.get("value_type")
        if not isinstance(parameter_key, str) or not parameter_key.strip():
            _projection_gap("actual_execution:invalid_parameter")
            continue
        if not isinstance(value_type, str) or value_type not in {
            "continuous", "integer", "categorical", "boolean"
        }:
            _projection_gap("actual_execution:invalid_parameter")
            continue
        value = row.get("value")
        if value_type == "continuous" and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            _projection_gap("actual_execution:invalid_value")
            continue
        if value_type == "integer" and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            _projection_gap("actual_execution:invalid_value")
            continue
        if value_type == "categorical" and (
            not isinstance(value, str) or not value.strip()
        ):
            _projection_gap("actual_execution:invalid_value")
            continue
        if value_type == "boolean" and not isinstance(value, bool):
            _projection_gap("actual_execution:invalid_parameter")
            continue
        rows.append(
            {
                "parameter_key": parameter_key,
                "value_type": value_type,
                "value": deepcopy(value),
                "unit": row.get("unit") if isinstance(row.get("unit"), str) else None,
                "unit_identity": (
                    row.get("unit_identity")
                    if isinstance(row.get("unit_identity"), str)
                    else None
                ),
                "unit_dimension": (
                    row.get("unit_dimension")
                    if isinstance(row.get("unit_dimension"), str)
                    else None
                ),
                "provenance": {
                    "origin": "user",
                    "source_ref": source_record_ref,
                },
            }
        )
    return rows


def _state_measurements(
    values: Any,
    *,
    experiment_ref: str,
    source_record_ref: str,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        _projection_gap("measurements:not_list")
        return []
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(values, start=1):
        if not isinstance(row, Mapping):
            _projection_gap(f"measurements:invalid_row:{index}")
            continue
        metric = row.get("metric")
        if not isinstance(metric, str) or not metric.strip():
            _projection_gap(f"measurements:invalid_row:{index}")
            continue
        state_row: dict[str, Any] = {
            "measurement_ref": f"{experiment_ref}:measurement:{index}",
            "metric_key": metric,
            # Query-reported measurements are real observations but the query does
            # not establish the platform's final/provisional validation status.
            "status": "provisional",
            "validity": "unknown",
            "provenance": {
                "origin": "user",
                "source_ref": source_record_ref,
            },
        }
        quantity = row.get("quantity")
        qualitative = row.get("qualitative_result")
        if isinstance(quantity, Mapping):
            if "value_numeric" in quantity:
                state_row["value"] = deepcopy(quantity["value_numeric"])
            else:
                state_row["value"] = str(quantity.get("value_raw") or "")
            unit_raw = quantity.get("unit_raw")
            state_row["unit"] = unit_raw if isinstance(unit_raw, str) else None
        elif isinstance(qualitative, str) and qualitative.strip():
            state_row["qualitative_result"] = qualitative.strip()
            state_row["unit"] = None
        else:
            _projection_gap(f"measurements:invalid_row:{index}")
            continue
        rows.append(state_row)
    return rows


def _query_derived_experiment_records(
    *,
    structured_context: Mapping[str, Any],
    context_ref: str,
    existing_by_source_record_ref: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rounds = structured_context.get("rounds")
    if not isinstance(rounds, list):
        _projection_gap("rounds:not_list")
        return []
    records: list[dict[str, Any]] = []
    seen_source_record_refs: set[str] = set()
    for row in rounds:
        if not isinstance(row, Mapping):
            _projection_gap("rounds:row_not_object")
            continue
        # 407b query-history semantics admit only actual executed/measured rounds
        # into existing experiment history. Planned rounds remain in the Artifact.
        status = row.get("status")
        if not isinstance(status, str) or status not in {"executed", "measured"}:
            continue
        actual = row.get("actual_execution")
        measurements = row.get("measurements")
        source_record_ref = row.get("source_record_ref")
        if (
            not isinstance(source_record_ref, str)
            or _QUERY_SOURCE_RECORD_REF.fullmatch(source_record_ref) is None
        ):
            raise ValueError("pvd_research_request_source_identity_invalid")
        if source_record_ref in seen_source_record_refs:
            raise ValueError("pvd_research_request_source_identity_duplicate")
        seen_source_record_refs.add(source_record_ref)
        existing = existing_by_source_record_ref.get(source_record_ref)
        if isinstance(existing, Mapping):
            existing_ref = existing.get("experiment_ref")
            if not isinstance(existing_ref, str) or not existing_ref.strip():
                raise ValueError("pvd_research_request_history_invalid")
            experiment_ref = existing_ref
        else:
            experiment_ref = f"pvd-experiment:{uuid4()}"
        actual_parameters = _state_parameter_values(
            actual,
            source_record_ref=source_record_ref,
        )
        state_measurements = _state_measurements(
            measurements,
            experiment_ref=experiment_ref,
            source_record_ref=source_record_ref,
        )
        if not actual_parameters and not state_measurements:
            _projection_gap(f"rounds:no_usable_observation:{source_record_ref}")
            continue
        records.append(
            {
                "experiment_ref": experiment_ref,
                "round_ref": (
                    row.get("round_ref")
                    if isinstance(row.get("round_ref"), str)
                    and row.get("round_ref").strip()
                    else None
                ),
                "experimental_system_context_ref": context_ref,
                "system_recommendation": None,
                "planned_execution": None,
                "actual_execution": {
                    "execution_ref": f"{experiment_ref}:actual",
                    "status": "executed",
                    "validity": "unknown",
                    "parameters": actual_parameters,
                    "other_process_conditions": [],
                    "provenance": {
                        "origin": "user",
                        "source_ref": source_record_ref,
                    },
                },
                "measurements": state_measurements,
                "process_events": [],
            }
        )
    return records


def materialize_experimental_system_context(
    *,
    task_payload: Mapping[str, Any],
    projected_facts: Mapping[str, Any],
    source_artifact_id: str,
    research_design_version: int,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Append one immutable current ScientificState when PVD context changed."""

    context_value = projected_facts.get("experimental_system_context")
    if not isinstance(context_value, Mapping):
        _projection_gap("experimental_system_context:not_object")
        return deepcopy(dict(task_payload))

    update = prepare_scientific_state_update(
        task_payload,
        source_research_design_version=research_design_version,
    )
    facts = _validated_composable_pvd_facts(existing_scientific_facts(update))

    context_ref = _upsert_context(
        facts=facts,
        context_value=context_value,
        source_artifact_id=source_artifact_id,
    )
    facts["current_experimental_system_context_ref"] = context_ref
    return append_scientific_state_if_changed(
        update,
        scientific_facts=facts,
        source_artifact_id=source_artifact_id,
        occurred_at=occurred_at,
    )


def materialize_research_request_understanding(
    *,
    task_payload: Mapping[str, Any],
    projected_facts: Mapping[str, Any],
    source_artifact_id: str,
    research_design_version: int,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Materialize current system facts plus query-derived real experiment history."""

    structured_context = projected_facts.get("pvd_research_request_context")
    if not isinstance(structured_context, Mapping):
        _projection_gap("pvd_research_request_context:not_object")
        return deepcopy(dict(task_payload))
    context_value = structured_context.get("experimental_system_context")
    if not isinstance(context_value, Mapping):
        _projection_gap("experimental_system_context:not_object")
        return deepcopy(dict(task_payload))

    update = prepare_scientific_state_update(
        task_payload,
        source_research_design_version=research_design_version,
    )
    facts = _validated_composable_pvd_facts(existing_scientific_facts(update))
    context_ref = _upsert_context(
        facts=facts,
        context_value=context_value,
        source_artifact_id=source_artifact_id,
    )
    facts["current_experimental_system_context_ref"] = context_ref

    history = facts["experiment_history"]
    existing_by_source_record_ref: dict[str, Mapping[str, Any]] = {}
    for record in history:
        if not isinstance(record, Mapping):
            continue
        actual_execution = record.get("actual_execution")
        provenance = (
            actual_execution.get("provenance")
            if isinstance(actual_execution, Mapping)
            else None
        )
        source_record_ref = (
            provenance.get("source_ref")
            if isinstance(provenance, Mapping)
            else None
        )
        if (
            not isinstance(source_record_ref, str)
            or _QUERY_SOURCE_RECORD_REF.fullmatch(source_record_ref) is None
        ):
            continue
        if source_record_ref in existing_by_source_record_ref:
            raise ValueError("pvd_research_request_source_identity_duplicate")
        existing_by_source_record_ref[source_record_ref] = record

    new_records = _query_derived_experiment_records(
        structured_context=structured_context,
        context_ref=context_ref,
        existing_by_source_record_ref=existing_by_source_record_ref,
    )
    by_ref = {
        row.get("experiment_ref"): row
        for row in history
        if isinstance(row, Mapping) and isinstance(row.get("experiment_ref"), str)
    }
    for row in new_records:
        experiment_ref = row["experiment_ref"]
        previous = by_ref.get(experiment_ref)
        if previous is None:
            history.append(row)
            by_ref[experiment_ref] = row
        else:
            if previous != row:
                # Tolerate idempotent retries where LLM output varies slightly in
                # formatting or field order. Keep the first-seen record to ensure
                # deterministic history across retries/recovery.
                logging.getLogger(__name__).warning(
                    "pvd_research_request_history_conflict experiment_ref=%s (keeping first-seen record)",
                    experiment_ref,
                )
                # Do not append; previous record stays

    return append_scientific_state_if_changed(
        update,
        scientific_facts=facts,
        source_artifact_id=source_artifact_id,
        occurred_at=occurred_at,
    )


def _materialized_recommendation(
    *,
    experiment: Any,
    next_step: str,
    source_artifact_id: str,
) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(experiment, Mapping):
        _projection_gap("recommendation:experiment_not_object")
        return None
    experiment_ref = experiment.get("experiment_id")
    validation_goal = experiment.get("validation_goal")
    failure_signal = experiment.get("failure_signal")
    parameters = experiment.get("parameters")
    if (
        not isinstance(experiment_ref, str)
        or not experiment_ref.strip()
        or not isinstance(validation_goal, str)
        or not validation_goal.strip()
        or not isinstance(failure_signal, str)
        or not failure_signal.strip()
        or not isinstance(parameters, list)
        or not parameters
    ):
        _projection_gap("recommendation:missing_core_fields")
        return None

    materialized_parameters: list[dict[str, Any]] = []
    seen_parameter_keys: set[str] = set()
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            _projection_gap("recommendation:parameter_not_object")
            continue
        parameter_key = parameter.get("parameter_key")
        display_name = parameter.get("display_name")
        recommended_value = parameter.get("recommended_value")
        unit = parameter.get("unit")
        if (
            not isinstance(parameter_key, str)
            or not parameter_key.strip()
            or parameter_key in seen_parameter_keys
            or (
                not isinstance(recommended_value, (str, int, float, bool))
                or (isinstance(recommended_value, str) and not recommended_value.strip())
            )
            or (display_name is not None and not isinstance(display_name, str))
            or (unit is not None and not isinstance(unit, str))
        ):
            _projection_gap("recommendation:invalid_parameter")
            continue
        seen_parameter_keys.add(parameter_key)
        materialized_parameters.append(
            {
                "parameter_key": parameter_key,
                "display_name": display_name,
                "recommended_value": deepcopy(recommended_value),
                "unit": unit,
            }
        )

    if not materialized_parameters:
        _projection_gap(f"recommendation:no_usable_parameters:{experiment_ref}")
        return None
    return experiment_ref, {
        "parameters": materialized_parameters,
        "validation_goal": validation_goal,
        "failure_signal": failure_signal,
        "next_step": next_step,
        "provenance": {
            "origin": "system",
            "source_ref": source_artifact_id,
        },
    }


def _recommendation_decision_complete(
    decision_status: Any,
    first_run_ranking_status: Any,
    experiments: Any,
    blocked_experiments: Any,
    next_step: Any,
    important_unknowns: Any,
) -> bool:
    complete = (
        isinstance(decision_status, str)
        and decision_status in {"recommendation", "no_recommendation"}
        and isinstance(first_run_ranking_status, str)
        and first_run_ranking_status in {"not_applicable", "pending", "ranked"}
        and isinstance(experiments, list)
        and _valid_blocked_experiments(blocked_experiments)
        and isinstance(next_step, str)
        and isinstance(important_unknowns, list)
        and (
            (decision_status == "no_recommendation" and not experiments
             and first_run_ranking_status == "not_applicable")
            or (decision_status == "recommendation" and bool(experiments)
                and first_run_ranking_status != "not_applicable")
        )
    )
    if not complete:
        _projection_gap("recommendation:decision_metadata_incomplete")
    return complete


def materialize_round_experiment_recommendation(
    *,
    task_payload: Mapping[str, Any],
    projected_facts: Mapping[str, Any],
    source_artifact_id: str,
    research_design_version: int,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Materialize an accepted Capability 7 decision into canonical PVD facts."""

    if not source_artifact_id.strip():
        raise ValueError("pvd_scientific_state_source_identity_missing")
    decision_status = projected_facts.get("pvd_recommendation_decision_status")
    first_run_ranking_status = projected_facts.get(
        "pvd_recommendation_first_run_ranking_status",
        "not_applicable" if decision_status == "no_recommendation" else "pending",
    )
    experiments = projected_facts.get("pvd_recommendation_experiments")
    blocked_experiments = projected_facts.get("pvd_recommendation_blocked_experiments", [])
    next_step = projected_facts.get("pvd_recommendation_next_step")
    important_unknowns = projected_facts.get("pvd_recommendation_important_unknowns")
    decision_complete = _recommendation_decision_complete(
        decision_status, first_run_ranking_status, experiments,
        blocked_experiments, next_step, important_unknowns,
    )
    can_project_experiments = (
        decision_status == "recommendation"
        and isinstance(experiments, list)
        and isinstance(next_step, str)
    )
    if not decision_complete and not can_project_experiments:
        return deepcopy(dict(task_payload))

    update = prepare_scientific_state_update(
        task_payload,
        source_research_design_version=research_design_version,
    )
    facts = _validated_composable_pvd_facts(existing_scientific_facts(update))
    if decision_complete:
        facts["current_recommendation_decision"] = {
            "decision_status": decision_status,
            "first_run_ranking_status": first_run_ranking_status,
            "blocked_experiments": deepcopy(blocked_experiments),
            "important_unknowns": deepcopy(important_unknowns),
            "next_step": next_step,
            "provenance": {
                "origin": "system",
                "source_ref": source_artifact_id,
            },
        }
    if decision_status == "no_recommendation":
        return append_scientific_state_if_changed(
            update,
            scientific_facts=facts,
            source_artifact_id=source_artifact_id,
            occurred_at=occurred_at,
        )

    current_context_ref = facts["current_experimental_system_context_ref"]
    history = facts["experiment_history"]
    existing_by_ref: dict[str, dict[str, Any]] = {}
    for record in history:
        if not isinstance(record, dict):
            raise ValueError("pvd_scientific_state_existing_facts_invalid")
        experiment_ref = record.get("experiment_ref")
        if (
            not isinstance(experiment_ref, str)
            or not experiment_ref.strip()
            or experiment_ref in existing_by_ref
        ):
            raise ValueError("pvd_scientific_state_existing_facts_invalid")
        existing_by_ref[experiment_ref] = record

    seen_result_refs: set[str] = set()
    for experiment in experiments:
        materialized = _materialized_recommendation(
            experiment=experiment,
            next_step=next_step,
            source_artifact_id=source_artifact_id,
        )
        if materialized is None:
            continue
        experiment_ref, recommendation = materialized
        if experiment_ref in seen_result_refs:
            raise ValueError("pvd_recommendation_conflict")
        seen_result_refs.add(experiment_ref)
        existing = existing_by_ref.get(experiment_ref)
        if existing is None:
            record = {
                "experiment_ref": experiment_ref,
                "round_ref": None,
                "experimental_system_context_ref": current_context_ref,
                "system_recommendation": recommendation,
                "planned_execution": None,
                "actual_execution": None,
                "measurements": [],
                "process_events": [],
            }
            history.append(record)
            existing_by_ref[experiment_ref] = record
            continue
        if existing.get("experimental_system_context_ref") != current_context_ref:
            raise ValueError("pvd_recommendation_conflict")
        accepted = existing.get("system_recommendation")
        if accepted is None:
            existing["system_recommendation"] = recommendation
        elif accepted != recommendation:
            raise ValueError("pvd_recommendation_conflict")

    return append_scientific_state_if_changed(
        update,
        scientific_facts=facts,
        source_artifact_id=source_artifact_id,
        occurred_at=occurred_at,
    )


def _merge_immutable_fact(
    record: dict[str, Any],
    *,
    field: str,
    submitted: Any,
) -> None:
    if submitted is None:
        return
    existing = record.get(field)
    if existing is None:
        record[field] = deepcopy(submitted)
        return
    if existing != submitted:
        raise ValueError(f'pvd_experiment_feedback_{field}_conflict')


def _append_ref_keyed_facts(
    record: dict[str, Any],
    *,
    field: str,
    ref_field: str,
    submitted: Any,
) -> None:
    if not isinstance(submitted, list):
        raise ValueError('pvd_experiment_feedback_invalid')
    existing = record.get(field)
    if not isinstance(existing, list):
        raise ValueError('pvd_scientific_state_existing_facts_invalid')
    existing_by_ref: dict[str, Any] = {}
    for row in existing:
        ref = row.get(ref_field) if isinstance(row, Mapping) else None
        if not isinstance(ref, str) or not ref.strip() or ref in existing_by_ref:
            raise ValueError('pvd_scientific_state_existing_facts_invalid')
        existing_by_ref[ref] = row
    submitted_refs: set[str] = set()
    for row in submitted:
        ref = row.get(ref_field) if isinstance(row, Mapping) else None
        if not isinstance(ref, str) or not ref.strip() or ref in submitted_refs:
            raise ValueError('pvd_experiment_feedback_invalid')
        submitted_refs.add(ref)
        prior = existing_by_ref.get(ref)
        if prior is None:
            copied = deepcopy(dict(row))
            existing.append(copied)
            existing_by_ref[ref] = copied
        elif prior != row:
            raise ValueError(f'pvd_experiment_feedback_{field}_conflict')


def materialize_experiment_feedback(
    *,
    task_payload: Mapping[str, Any],
    feedback_facts: Mapping[str, Any],
    source_ref: str,
    research_design_version: int,
    occurred_at: datetime,
) -> dict[str, Any]:
    '''Merge formal real-world feedback into one canonical experiment record.'''

    expected_keys = {
        'experiment_ref',
        'provenance',
        'planned_execution',
        'actual_execution',
        'measurements',
        'process_events',
    }
    if set(feedback_facts) != expected_keys or not source_ref.strip():
        raise ValueError('pvd_experiment_feedback_invalid')
    experiment_ref = feedback_facts.get('experiment_ref')
    provenance = feedback_facts.get('provenance')
    if (
        not isinstance(experiment_ref, str)
        or not experiment_ref.strip()
        or not isinstance(provenance, Mapping)
        or set(provenance) != {'origin', 'source_ref'}
        or provenance.get('origin') not in {'user', 'imported_project_record'}
        or provenance.get('source_ref') != source_ref
    ):
        raise ValueError('pvd_experiment_feedback_invalid')
    if (
        feedback_facts.get('planned_execution') is None
        and feedback_facts.get('actual_execution') is None
        and not feedback_facts.get('measurements')
        and not feedback_facts.get('process_events')
    ):
        raise ValueError('pvd_experiment_feedback_empty')

    update = prepare_scientific_state_update(
        task_payload,
        source_research_design_version=research_design_version,
    )
    facts = _validated_composable_pvd_facts(existing_scientific_facts(update))
    _validate_pvd_state_facts(facts)
    history = facts['experiment_history']
    matches = [
        row
        for row in history
        if isinstance(row, dict) and row.get('experiment_ref') == experiment_ref
    ]
    if len(matches) != 1:
        raise ValueError(
            'pvd_experiment_feedback_experiment_not_found'
            if not matches
            else 'pvd_scientific_state_existing_facts_invalid'
        )
    record = matches[0]
    _merge_immutable_fact(
        record,
        field='planned_execution',
        submitted=feedback_facts.get('planned_execution'),
    )
    _merge_immutable_fact(
        record,
        field='actual_execution',
        submitted=feedback_facts.get('actual_execution'),
    )
    _append_ref_keyed_facts(
        record,
        field='measurements',
        ref_field='measurement_ref',
        submitted=feedback_facts.get('measurements'),
    )
    _append_ref_keyed_facts(
        record,
        field='process_events',
        ref_field='event_ref',
        submitted=feedback_facts.get('process_events'),
    )
    _validate_pvd_state_facts(facts)
    return append_scientific_state_if_changed(
        update,
        scientific_facts=facts,
        source_artifact_id=source_ref,
        occurred_at=occurred_at,
    )


__all__ = [
    'materialize_experiment_feedback',
    "PVD_EXPERIMENTAL_SYSTEM_CONTEXT_MATERIALIZER",
    "PVD_RESEARCH_REQUEST_MATERIALIZER",
    "PVD_ROUND_EXPERIMENT_RECOMMENDATION_MATERIALIZER",
    "materialize_experimental_system_context",
    "materialize_round_experiment_recommendation",
    "materialize_research_request_understanding",
]

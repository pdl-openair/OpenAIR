from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


def _find_repository_root(start: Path) -> Path:
    directory = start if start.is_dir() else start.parent
    matches = [candidate for candidate in (directory, *directory.parents) if (candidate / "schemas" / "pvd").is_dir()]
    if len(matches) != 1:
        raise RuntimeError(
            "cannot resolve unique OpenAIR repository root containing schemas/pvd; "
            f"found {len(matches)} candidates from {start}"
        )
    return matches[0]


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = _find_repository_root(Path(__file__).resolve())
STATE_SCHEMA_PATH = SOURCE_ROOT / "schemas/pvd/pvd-scientific-state-facts-v1.schema.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_registry() -> Registry:
    state_schema = load_json(STATE_SCHEMA_PATH)
    return Registry().with_resource(
        state_schema["$id"], Resource.from_contents(state_schema)
    )


def validate_public_input(payload: dict) -> None:
    schema = load_json(ROOT / "input.schema.json")
    Draft202012Validator(schema, registry=schema_registry()).validate(payload)


def validate_state_facts(facts: dict) -> None:
    schema = load_json(STATE_SCHEMA_PATH)
    Draft202012Validator(schema, registry=schema_registry()).validate(facts)


def known_meta(source_ref: str = "artifact:node1:context:1") -> dict:
    return {
        "status": "known",
        "provenance": [{
            "source_id": source_ref,
            "raw_expression": "explicit experimental-system fact",
        }],
    }


def system_context(
    *,
    substrate: str | None = "Mo",
    target_angle: str | None = "内偏30度",
    target_material: str | None = "40%AlSc",
    distance_mm: float | None = 110,
    power_supply_temperature: float | None = 200,
) -> dict:
    values = {
        "target_material": target_material,
        "substrate": substrate,
        "target_substrate_distance_mm": distance_mm,
        "power_supply_temperature": power_supply_temperature,
        "target_angle": target_angle,
    }
    metadata = {}
    for key, value in values.items():
        metadata[key] = (
            known_meta()
            if value is not None
            else {"status": "missing", "reason": "not provided", "provenance": []}
        )
    status = "complete" if all(value is not None for value in values.values()) else "partial"
    return {
        "contract": "pvd-experimental-system-context/v1",
        "context_status": status,
        **values,
        "field_metadata": metadata,
        "additional_facts": [],
    }


def parameter(value: float, unit: str, source_ref: str) -> dict:
    return {
        "status": "known",
        "value": value,
        "unit": unit,
        "provenance": {"origin": "user", "source_ref": source_ref},
    }


def actual_execution(
    *,
    execution_ref: str = "EXEC-1",
    status: str = "executed",
    validity: str = "valid",
    power: float = 200,
    n2_flow: float = 17,
    ar_flow: float = 39,
    deposition_time: float = 2300,
    source_ref: str = "record:experiment:1",
) -> dict:
    return {
        "execution_ref": execution_ref,
        "status": status,
        "validity": validity,
        "parameters": {
            "power": parameter(power, "W", source_ref),
            "n2_flow": parameter(n2_flow, "sccm", source_ref),
            "ar_flow": parameter(ar_flow, "sccm", source_ref),
            "deposition_time": parameter(deposition_time, "s", source_ref),
        },
        "other_process_conditions": [],
        "provenance": {"origin": "user", "source_ref": source_ref},
    }


def measurement(
    *,
    measurement_ref: str = "MEAS-1",
    metric_key: str = "d33",
    value: float = -15.2,
    status: str = "final",
    validity: str = "valid",
    source_ref: str = "record:experiment:1",
) -> dict:
    return {
        "measurement_ref": measurement_ref,
        "metric_key": metric_key,
        "value": value,
        "unit": "pC/N",
        "status": status,
        "validity": validity,
        "method": None,
        "measurement_condition": None,
        "evaluation_basis_ref": None,
        "repeat_group_ref": None,
        "uncertainty": None,
        "reported_anomaly": None,
        "provenance": {"origin": "user", "source_ref": source_ref},
    }


def experiment(
    *,
    experiment_ref: str = "EXP-1",
    context_ref: str = "SYS-A",
    round_ref: str | None = "R1",
    actual: dict | None = None,
    measurements: list[dict] | None = None,
    recommendation: dict | None = None,
    planned: dict | None = None,
) -> dict:
    return {
        "experiment_ref": experiment_ref,
        "round_ref": round_ref,
        "experimental_system_context_ref": context_ref,
        "system_recommendation": recommendation,
        "planned_execution": planned,
        "actual_execution": actual if actual is not None else actual_execution(),
        "measurements": measurements if measurements is not None else [measurement()],
        "process_events": [],
    }


def state_facts(
    *,
    current_ref: str = "SYS-A",
    current_context: dict | None = None,
    history: list[dict] | None = None,
    extra_contexts: list[dict] | None = None,
) -> dict:
    contexts = [
        {
            "experimental_system_context_ref": current_ref,
            "experimental_system_context": current_context or system_context(),
        }
    ]
    if extra_contexts:
        contexts.extend(deepcopy(extra_contexts))
    return {
        "experimental_system_contexts": contexts,
        "current_experimental_system_context_ref": current_ref,
        "experiment_history": deepcopy(history) if history is not None else [experiment(context_ref=current_ref)],
    }


def request_payload(**kwargs) -> dict:
    return {"scientific_state_facts": state_facts(**kwargs)}

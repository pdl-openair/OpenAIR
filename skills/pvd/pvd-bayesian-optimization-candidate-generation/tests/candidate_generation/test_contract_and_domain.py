from __future__ import annotations

import json

import pytest
from helpers import (
    ROOT,
    actual_execution,
    experiment,
    measurement,
    request_payload,
    system_context,
    validate_public_input,
)
from jsonschema import ValidationError
from pvd_bo.service import run_candidate_generation


def _load_example(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def test_public_contract_accepts_exact_canonical_state_only() -> None:
    payload = _load_example("current_system_request.json")
    validate_public_input(payload)
    assert set(payload) == {"scientific_state_facts"}


@pytest.mark.parametrize("legacy_key", ["model_profile_id", "process_context", "project_observations"])
def test_public_contract_rejects_legacy_bo_specific_state_views(legacy_key: str) -> None:
    payload = _load_example("current_system_request.json")
    payload[legacy_key] = {} if legacy_key != "model_profile_id" else "legacy"
    with pytest.raises(ValidationError):
        validate_public_input(payload)








def test_cross_system_history_is_excluded_not_silently_mixed() -> None:
    current = experiment(experiment_ref="EXP-CURRENT", context_ref="SYS-A")
    other = experiment(
        experiment_ref="EXP-OTHER",
        context_ref="SYS-B",
        actual=actual_execution(execution_ref="EXEC-OTHER", source_ref="record:other"),
        measurements=[measurement(measurement_ref="MEAS-OTHER", source_ref="record:other")],
    )
    payload = request_payload(
        history=[other, current],
        extra_contexts=[{
            "experimental_system_context_ref": "SYS-B",
            "experimental_system_context": system_context(substrate="Si"),
        }],
    )
    validate_public_input(payload)
    result = run_candidate_generation(payload)
    assert result["candidate_generation_status"] == "generated"
    assert result["training_summary"]["project_count"] == 1

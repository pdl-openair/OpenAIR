from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
from helpers import state_facts
from pvd_bo.applicability import (
    EXECUTION_INSTANCE_FACT_REF,
    RESEARCH_TASK_FACT_REF,
    SCIENTIFIC_STATE_FACT_REF,
    evaluate_capability_applicability,
)

CAPABILITY_REF = "skill:pvd_bayesian_optimization_candidate_generation:1"
EVALUATOR_REF = "python:pvd_bo.applicability:evaluate_capability_applicability"
FACT_REFS = sorted(
    [RESEARCH_TASK_FACT_REF, EXECUTION_INSTANCE_FACT_REF, SCIENTIFIC_STATE_FACT_REF]
)


def _request(*, facts=None, reentry=True) -> dict:
    instance_id = "instance:generic-repeatable:2"
    state_source = "artifact:validated-feedback"
    return {
        "capabilityRef": CAPABILITY_REF,
        "evaluatorRef": EVALUATOR_REF,
        "runtimeFacts": {
            RESEARCH_TASK_FACT_REF: {
                "taskId": "task-1",
                "reentryExecutionInstances": (
                    [{
                        "instance": {"substageInstanceId": instance_id},
                        "triggeringScientificStateVersion": 7,
                        "triggeringStateSourceRefs": [state_source],
                    }]
                    if reentry
                    else []
                ),
            },
            EXECUTION_INSTANCE_FACT_REF: {"substageInstanceId": instance_id},
            SCIENTIFIC_STATE_FACT_REF: {
                "scientificStateVersion": 7,
                "scientificFacts": facts if facts is not None else state_facts(),
                "sourceRefs": [state_source],
            },
        },
    }


def _assert_typed(result: dict, status: str) -> None:
    assert result == {
        "capabilityRef": CAPABILITY_REF,
        "evaluatorRef": EVALUATOR_REF,
        "status": status,
        "evaluatedRuntimeFactRefs": FACT_REFS,
    }


def test_same_system_real_feedback_returns_typed_eligible() -> None:
    _assert_typed(evaluate_capability_applicability(_request()), "eligible")




















def test_entrypoint_does_not_run_candidate_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def reject_candidate_service_import(name, *args, **kwargs):
        if name == "pvd_bo.service" or name.startswith("pvd_bo.service."):
            raise AssertionError("candidate generation must not be imported")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_candidate_service_import)
    _assert_typed(evaluate_capability_applicability(_request()), "eligible")


def test_identity_fields_are_echoed_not_hard_coded() -> None:
    request = _request()
    request["capabilityRef"] = "skill:alternate-definition:3"
    result = evaluate_capability_applicability(request)
    assert result["capabilityRef"] == request["capabilityRef"]
    assert result["status"] == "eligible"




def test_capability_owned_evaluator_descriptor_is_machine_readable() -> None:
    root = Path(__file__).resolve().parents[2]
    descriptor = json.loads((root / "applicability_evaluator.json").read_text(encoding="utf-8"))
    assert descriptor["evaluatorRef"] == EVALUATOR_REF
    assert sorted(descriptor["requiredRuntimeFactRefs"]) == FACT_REFS
    assert descriptor["scientificStateFactPath"] == "scientificFacts"
    assert descriptor["scientificStateFactSchema"] == "applicability_runtime_fact.schema.json"
    assert descriptor["modelProfileId"] == "al40sc_mo_110mm_v1"


def test_applicability_fact_schema_reuses_canonical_pvd_state_facts() -> None:
    root = Path(__file__).resolve().parents[2]
    runtime_schema = json.loads((root / "applicability_runtime_fact.schema.json").read_text(encoding="utf-8"))
    assert runtime_schema["$ref"] == "https://openair.local/schemas/pvd/pvd-scientific-state-facts-v1.schema.json"

from __future__ import annotations

from reproduction_execution_input_closure.applicability import (
    BINDING_INPUT_READINESS_FACT_REF,
    evaluate_capability_applicability,
)


def _request(status: str | None) -> dict:
    readiness = {} if status is None else {"status": status}
    return {
        "capabilityRef": "skill:reproduction_execution_input_closure:1",
        "evaluatorRef": (
            "python:reproduction_execution_input_closure.applicability:"
            "evaluate_capability_applicability"
        ),
        "runtimeFacts": {BINDING_INPUT_READINESS_FACT_REF: readiness},
    }


def test_ready_is_eligible() -> None:
    result = evaluate_capability_applicability(_request("ready"))
    assert result["status"] == "eligible"


def test_formal_non_ready_is_ineligible() -> None:
    for status in ("not_ready", "partially_ready"):
        result = evaluate_capability_applicability(_request(status))
        assert result["status"] == "ineligible", status


def test_unknown_status_fails_closed() -> None:
    result = evaluate_capability_applicability(_request("blocked"))
    assert result["status"] == "evaluation_failed"

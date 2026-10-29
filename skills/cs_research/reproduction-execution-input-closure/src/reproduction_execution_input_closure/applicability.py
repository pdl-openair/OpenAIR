"""Fail-closed ready-only gate for reproduction execution input closure."""

from __future__ import annotations

from typing import Any, Mapping


BINDING_INPUT_READINESS_FACT_REF = "binding-input:execution_readiness_record"

ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
EVALUATION_FAILED = "evaluation_failed"

_READY = "ready"
_FORMAL_NON_READY = frozenset({"not_ready", "partially_ready"})


def _typed_result(
    *,
    capability_ref: str,
    evaluator_ref: str,
    status: str,
    evaluated_fact_refs: list[str],
) -> dict[str, Any]:
    return {
        "capabilityRef": capability_ref,
        "evaluatorRef": evaluator_ref,
        "status": status,
        "evaluatedRuntimeFactRefs": evaluated_fact_refs,
    }


def evaluate_capability_applicability(request: Mapping[str, Any]) -> dict[str, Any]:
    """Map Producer readiness to Binding-boundary ready|blocked eligibility.

    Producer keeps ``ready | not_ready | partially_ready``.  At the Binding
    boundary only ``ready`` releases the Consumer; every other formal readiness
    status is treated as blocked and withheld.
    """

    if not isinstance(request, Mapping):
        raise ValueError("applicability request must be an object")

    capability_ref = str(request.get("capabilityRef") or "").strip()
    evaluator_ref = str(request.get("evaluatorRef") or "").strip()
    if not capability_ref or not evaluator_ref:
        raise ValueError("capabilityRef and evaluatorRef are required")

    runtime_facts = request.get("runtimeFacts")
    expected_fact_refs = [BINDING_INPUT_READINESS_FACT_REF]
    if not isinstance(runtime_facts, Mapping):
        return _typed_result(
            capability_ref=capability_ref,
            evaluator_ref=evaluator_ref,
            status=EVALUATION_FAILED,
            evaluated_fact_refs=[],
        )
    fact_refs = sorted(str(ref) for ref in runtime_facts)
    if fact_refs != expected_fact_refs:
        return _typed_result(
            capability_ref=capability_ref,
            evaluator_ref=evaluator_ref,
            status=EVALUATION_FAILED,
            evaluated_fact_refs=[],
        )

    readiness = runtime_facts.get(BINDING_INPUT_READINESS_FACT_REF)
    status_value = readiness.get("status") if isinstance(readiness, Mapping) else None
    if status_value == _READY:
        status = ELIGIBLE
    elif status_value in _FORMAL_NON_READY:
        status = INELIGIBLE
    else:
        status = EVALUATION_FAILED

    return _typed_result(
        capability_ref=capability_ref,
        evaluator_ref=evaluator_ref,
        status=status,
        evaluated_fact_refs=fact_refs,
    )


__all__ = [
    "BINDING_INPUT_READINESS_FACT_REF",
    "evaluate_capability_applicability",
]

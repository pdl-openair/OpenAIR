from __future__ import annotations

from typing import Any

SCIENTIFIC_STATE_FACT_REF = "runtime:scientific-state:v1"
RESEARCH_TASK_FACT_REF = "runtime:research-task:v1"
EXECUTION_INSTANCE_FACT_REF = "runtime:execution-instance:v1"

ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
EVALUATION_FAILED = "evaluation_failed"






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


def evaluate_capability_applicability(request):
    """Describe available runtime facts without a scientific eligibility gate."""
    return _typed_result(
        capability_ref=request["capabilityRef"], evaluator_ref=request["evaluatorRef"],
        status=ELIGIBLE, evaluated_fact_refs=sorted(request["runtimeFacts"]),
    )


__all__ = [
    "EXECUTION_INSTANCE_FACT_REF",
    "RESEARCH_TASK_FACT_REF",
    "SCIENTIFIC_STATE_FACT_REF",
    "evaluate_capability_applicability",
]

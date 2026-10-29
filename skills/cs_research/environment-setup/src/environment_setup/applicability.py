"""Withhold environment setup when upstream declares no install/import work."""

from __future__ import annotations

from typing import Any, Mapping


RESEARCH_TASK_FACT_REF = "runtime:research-task:v1"
ARTIFACT_FIELD_PROJECTION_KEY = "artifactFieldProjection"
DESIGN_ARTIFACT_TYPE = "reproduction_experiment_design"
REQUIREMENTS_FIELD = "requirements"
VERIFY_IMPORTS_FIELD = "verify_imports"

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


def _design_projections(research_task: Mapping[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for artifact in research_task.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        if artifact.get("artifactState") != "usable":
            continue
        if artifact.get("artifactType") != DESIGN_ARTIFACT_TYPE:
            continue
        validator = artifact.get("validatorResult")
        if not isinstance(validator, Mapping):
            continue
        projected = validator.get(ARTIFACT_FIELD_PROJECTION_KEY)
        if isinstance(projected, Mapping):
            matches.append(dict(projected))
    return matches


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def evaluate_capability_applicability(request: Mapping[str, Any]) -> dict[str, Any]:
    """Release only when upstream requests requirements or import verification.

    Explicit empty requirements and verify_imports together are a legal no-op
    and must not start this Role. Missing design material stays eligible so the
    input gate can still classify material_unavailable.
    """

    if not isinstance(request, Mapping):
        raise ValueError("applicability request must be an object")

    capability_ref = str(request.get("capabilityRef") or "").strip()
    evaluator_ref = str(request.get("evaluatorRef") or "").strip()
    if not capability_ref or not evaluator_ref:
        raise ValueError("capabilityRef and evaluatorRef are required")

    runtime_facts = request.get("runtimeFacts")
    expected_fact_refs = [RESEARCH_TASK_FACT_REF]
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

    research_task = runtime_facts.get(RESEARCH_TASK_FACT_REF)
    if not isinstance(research_task, Mapping):
        return _typed_result(
            capability_ref=capability_ref,
            evaluator_ref=evaluator_ref,
            status=EVALUATION_FAILED,
            evaluated_fact_refs=fact_refs,
        )

    projections = _design_projections(research_task)
    if not projections:
        status = ELIGIBLE
    elif len(projections) != 1:
        status = EVALUATION_FAILED
    else:
        requirements_len = _non_negative_int(projections[0].get(REQUIREMENTS_FIELD))
        imports_len = _non_negative_int(projections[0].get(VERIFY_IMPORTS_FIELD))
        if requirements_len == 0 and imports_len == 0:
            status = INELIGIBLE
        else:
            status = ELIGIBLE

    return _typed_result(
        capability_ref=capability_ref,
        evaluator_ref=evaluator_ref,
        status=status,
        evaluated_fact_refs=fact_refs,
    )


__all__ = [
    "RESEARCH_TASK_FACT_REF",
    "evaluate_capability_applicability",
]

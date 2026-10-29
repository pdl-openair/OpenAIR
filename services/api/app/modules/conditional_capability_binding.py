"""Generic fail-closed eligibility for frozen conditional Skill bindings."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from pydantic import ConfigDict, model_validator

from app.models.research_orchestration import ContractModel, NonEmptyStr
from app.modules.binding_input_runtime_projection import is_binding_input_fact_ref


POLICY_VERSION = "conditional_capability_binding_v1"


class CapabilityApplicabilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    EVALUATION_FAILED = "evaluation_failed"


class CapabilityApplicabilityResult(ContractModel):
    """Capability-owned result admitted through authoritative ResolutionFacts."""

    model_config = ConfigDict(frozen=True)

    capability_ref: NonEmptyStr
    evaluator_ref: NonEmptyStr
    status: CapabilityApplicabilityStatus
    evaluated_runtime_fact_refs: list[NonEmptyStr]

    @model_validator(mode="after")
    def validate_fact_refs(self) -> "CapabilityApplicabilityResult":
        if len(set(self.evaluated_runtime_fact_refs)) != len(
            self.evaluated_runtime_fact_refs
        ):
            raise ValueError("evaluatedRuntimeFactRefs must be unique")
        return self


def validate_conditional_policy(value: Any) -> dict[str, Any]:
    """Validate the minimal frozen v1 policy without interpreting a DSL."""

    if not isinstance(value, Mapping) or set(value) != {
        "policyVersion",
        "evaluatorRef",
        "requiredRuntimeFactRefs",
    }:
        raise ValueError("conditionalPolicy shape is invalid")
    if value.get("policyVersion") != POLICY_VERSION:
        raise ValueError("conditionalPolicy version is unsupported")
    evaluator_ref = value.get("evaluatorRef")
    required_refs = value.get("requiredRuntimeFactRefs")
    if not isinstance(evaluator_ref, str) or not evaluator_ref.strip():
        raise ValueError("conditionalPolicy evaluatorRef is invalid")
    if (
        not isinstance(required_refs, list)
        or not required_refs
        or any(not isinstance(row, str) or not row.strip() for row in required_refs)
        or len(set(required_refs)) != len(required_refs)
    ):
        raise ValueError("conditionalPolicy requiredRuntimeFactRefs are invalid")
    return {
        "policyVersion": POLICY_VERSION,
        "evaluatorRef": evaluator_ref,
        "requiredRuntimeFactRefs": list(required_refs),
    }


def evaluate_skill_binding_eligibility(
    binding: Mapping[str, Any],
    *,
    capability_ref: str,
    authoritative_runtime_fact_refs: set[str],
    applicability_results: Mapping[str, CapabilityApplicabilityResult],
) -> CapabilityApplicabilityStatus:
    """Evaluate one existing binding before it enters the Resolver candidate pool."""

    if binding.get("enabled") is not True:
        return CapabilityApplicabilityStatus.INELIGIBLE
    raw_policy = binding.get("conditionalPolicy")
    if raw_policy is None:
        return CapabilityApplicabilityStatus.ELIGIBLE
    try:
        policy = validate_conditional_policy(raw_policy)
    except ValueError:
        return CapabilityApplicabilityStatus.EVALUATION_FAILED
    required_refs = set(policy["requiredRuntimeFactRefs"])
    authoritative_required_refs = {
        ref for ref in required_refs if not is_binding_input_fact_ref(ref)
    }
    if not authoritative_required_refs <= authoritative_runtime_fact_refs:
        return CapabilityApplicabilityStatus.INELIGIBLE
    result = applicability_results.get(capability_ref)
    if (
        result is None
        or result.capability_ref != capability_ref
        or result.evaluator_ref != policy["evaluatorRef"]
        or not required_refs <= set(result.evaluated_runtime_fact_refs)
    ):
        return CapabilityApplicabilityStatus.EVALUATION_FAILED
    return result.status


__all__ = [
    "CapabilityApplicabilityResult",
    "CapabilityApplicabilityStatus",
    "POLICY_VERSION",
    "evaluate_skill_binding_eligibility",
    "validate_conditional_policy",
]

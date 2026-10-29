"""One confirmation rule for coverage that preserves unresolved target facts."""

from collections.abc import Mapping
from typing import Any


def _field(value: Any, snake: str, camel: str) -> Any:
    return (
        value.get(camel, value.get(snake))
        if isinstance(value, Mapping)
        else getattr(value, snake, None)
    )


def has_only_executable_target_gaps(report: Any) -> bool:
    if report is None:
        return False
    if _field(report, "overloaded_instance_refs", "overloadedInstanceRefs") or _field(
        report, "missing_dependency_refs", "missingDependencyRefs"
    ):
        return False
    targets = _field(report, "targets", "targets") or []
    claims = _field(report, "claims", "claims") or []
    uncovered = _field(report, "uncovered_required_target_ids", "uncoveredRequiredTargetIds") or []
    by_target = {_field(row, "target_id", "targetId"): row for row in targets}
    by_claim = {_field(row, "target_id", "targetId"): row for row in claims}
    if not uncovered or len(by_target) != len(targets) or set(by_target) != set(by_claim):
        return False
    if len(by_claim) != len(claims):
        return False
    for key, target in by_target.items():
        if _field(target, "source_type", "sourceType") == "target_output":
            continue
        claim = by_claim[key]
        if not _field(claim, "instance_refs", "instanceRefs") or not _field(
            claim, "acceptance_criterion_refs", "acceptanceCriterionRefs"
        ):
            return False
    incomplete = {
        key
        for key, row in by_claim.items()
        if _field(row, "coverage_mode", "coverageMode") != "full"
    }
    return incomplete == set(uncovered) and all(
        _field(by_target[key], "source_type", "sourceType") == "target_output" for key in incomplete
    )


def coverage_allows_confirmation(report: Any) -> bool:
    status = _field(report, "status", "status")
    if status in {"passed_structural", "passed_semantic"}:
        return True
    return status == "executable_with_gaps" and has_only_executable_target_gaps(report)

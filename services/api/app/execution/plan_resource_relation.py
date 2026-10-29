"""Deterministic authority for the formal plan to acquired code relationship."""

from __future__ import annotations

import re
from typing import Any, Mapping


_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def verify_plan_resource_relation(plan: Any, acquisition: Any) -> str:
    """Return verified, mismatch, or not_verified without trusting requested revision."""
    source = plan.get("source_identity") if isinstance(plan, Mapping) else None
    repository = source.get("repository") if isinstance(source, Mapping) else None
    commit = source.get("resolved_commit_sha") if isinstance(source, Mapping) else None
    if (
        not isinstance(repository, str) or not repository.strip()
        or not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None
        or not isinstance(acquisition, dict)
    ):
        return "not_verified"

    # The acquisition runtime owns the evidence contract, including HEAD verification.
    from app.execution.durable_skill_runtime import _has_repository_revision_acquisition_evidence

    if not _has_repository_revision_acquisition_evidence(acquisition):
        return "not_verified"
    if (acquisition.get("repository"), acquisition.get("resolved_commit_sha")) != (
        repository, commit
    ):
        return "mismatch"
    return "verified"


def enforce_readiness_relation(
    output: dict[str, Any], plan: Any, acquisition: Any
) -> dict[str, Any]:
    relation = verify_plan_resource_relation(plan, acquisition)
    result = dict(output)
    result["plan_resource_relation_status"] = relation
    if relation != "verified":
        result["status"] = "not_ready"
        result["executable_scope"] = []
        gaps = list(result.get("blocking_gaps") or [])
        relation_gap = {"code": "plan_resource_relation", "status": relation}
        if relation_gap not in gaps:
            gaps.append(relation_gap)
        result["blocking_gaps"] = gaps
    return result

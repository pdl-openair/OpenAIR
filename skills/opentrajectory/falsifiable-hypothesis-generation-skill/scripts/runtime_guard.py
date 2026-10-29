from __future__ import annotations

import copy
from typing import Any

from scripts.identity import DEFAULT_MAX_HYPOTHESES, build_hypothesis_id, build_input_fingerprint
from scripts.validation import (
    build_indexes,
    validate_draft,
    validate_final_output,
    validate_input,
)

GUARD_VERSION = "s1-hypothesis-v2"
SKILL_ID = "ot_falsifiable_hypothesis_generation"
CLAIM_TYPE = "unvalidated_falsifiable_hypothesis"
NOVELTY_BOUNDARY = "derived_from_research_lead_not_established_by_sources"
BASIS_TYPES = ("support_derived", "conflict_derived", "gap_derived", "mixed", "finding_only")
EVIDENCE_CONTEXTS = ("supported", "uncertain", "conflicting", "boundary_limited", "gap_limited")
SET_FIELDS = (
    "source_research_lead_ids",
    "source_finding_ids",
    "boundary_conditions",
    "provenance_evidence_ids",
    "provenance_mechanism_edge_ids",
    "uncertainties",
)


def _canonicalize(hypothesis: dict[str, Any]) -> dict[str, Any]:
    canonical = copy.deepcopy(hypothesis)
    for field in SET_FIELDS:
        canonical[field] = sorted(canonical[field])
    return canonical


def _provenance_basis(hypothesis, edges) -> str:
    edge_ids = hypothesis["provenance_mechanism_edge_ids"]
    if not edge_ids:
        return "finding_only"
    statuses = {edges[edge_id]["edge_status"] for edge_id in edge_ids if edge_id in edges}
    if statuses == {"support"}:
        return "support_derived"
    if statuses == {"conflict"}:
        return "conflict_derived"
    if statuses == {"gap"}:
        return "gap_derived"
    return "mixed"


def finalize_hypothesis(hypothesis, edges) -> dict[str, Any]:
    finalized = _canonicalize(hypothesis)
    finalized["hypothesis_id"] = build_hypothesis_id(finalized)
    finalized["claim_type"] = CLAIM_TYPE
    finalized["novelty_boundary"] = NOVELTY_BOUNDARY
    finalized["provenance_basis"] = _provenance_basis(finalized, edges)
    return finalized


def _max_hypotheses(input_payload) -> int:
    config = input_payload.get("generation_config")
    return DEFAULT_MAX_HYPOTHESES if config is None else config["max_hypotheses"]


def _build_summary(selected, *, lead_count, duplicate_count, over_limit_count, limit, degraded):
    basis_counts = {basis: 0 for basis in BASIS_TYPES}
    context_counts = {status: 0 for status in EVIDENCE_CONTEXTS}
    for hypothesis in selected:
        basis_counts[hypothesis["provenance_basis"]] += 1
        status = hypothesis["evidence_context_status"]
        context_counts[status] = context_counts.get(status, 0) + 1
    return {
        "input_research_lead_count": lead_count,
        "hypothesis_count": len(selected),
        "provenance_basis_counts": basis_counts,
        "evidence_context_counts": context_counts,
        "dropped_duplicate_hypothesis_count": duplicate_count,
        "dropped_over_limit_hypothesis_count": over_limit_count,
        "max_hypotheses": limit,
        "degraded": degraded,
    }


def finalize(input_payload: Any, draft_payload: Any) -> dict[str, Any]:
    input_payload = validate_input(input_payload)
    draft = validate_draft(draft_payload)
    edges, findings, leads = build_indexes(input_payload)

    finalized = []
    for index, hypothesis in enumerate(draft["hypotheses"]):
        finalized.append(finalize_hypothesis(hypothesis, edges))

    unique = finalized
    duplicate_count = 0
    limit = _max_hypotheses(input_payload)
    selected = [
        {**hypothesis, "local_hypothesis_key": f"hypothesis-{index}"}
        for index, hypothesis in enumerate(unique, start=1)
    ]
    degraded = limit > 0 and not selected
    warnings = copy.deepcopy(draft["generation_warnings"])

    result = {
        "hypotheses": selected,
        "summary": _build_summary(
            selected,
            lead_count=len(leads),
            duplicate_count=duplicate_count,
            over_limit_count=max(0, len(unique) - len(selected)),
            limit=limit,
            degraded=degraded,
        ),
        "audit": {
            "skill_id": SKILL_ID,
            "task_id": input_payload["task_id"],
            "traceability_mode": "upstream_source_binding",
            "guard_version": GUARD_VERSION,
            "input_fingerprint": build_input_fingerprint(input_payload),
            "claim_layer": CLAIM_TYPE,
            "novelty_boundary": NOVELTY_BOUNDARY,
        },
        "generation_warnings": warnings,
    }
    validate_final_output(result)
    return result

"""Adapter for candidate-tiering Skill.

Migrated from OpenTrajectory p025_topk_selector.py:
- _gate_and_pool() logic and related gating constants only.
(No top-k selection)
"""

from __future__ import annotations

from typing import Any

TIER_ENUM = [
    "core",
    "mechanism_support",
    "method_inspiration",
    "benchmark_or_evaluation",
    "background",
    "excluded",
]


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _classify_one(c: dict[str, Any]) -> dict[str, Any]:
    b = c.get("score_breakdown") or {}
    final_score = _f(c.get("final_score"))
    obj = _f(b.get("object_match"))
    mech = _f(b.get("mechanism_match"))
    metric = _f(b.get("metric_match"))
    ctx = _f(b.get("context_match"))
    eval_match = _f(b.get("evaluation_match"))
    neg = _f(b.get("negative_penalty"))
    off_domain_penalty = _f(b.get("off_domain_penalty"))

    neg_hits = c.get("negative_hits") if isinstance(c.get("negative_hits"), list) else []
    metric_only = bool(c.get("metric_only"))
    must_have_hit_count = int(c.get("must_have_hit_count") or 0)

    core_ok = (obj >= 0.4) and (mech >= 0.4 or must_have_hit_count > 0) and (ctx >= 0.3)

    tier = "background"
    pool = "background_pool"
    selection_reason = "low structural relevance"
    downrank_reason = None
    exclusion_reason = None

    # P08B-1E-c: off-domain check takes priority
    if off_domain_penalty >= 0.6:
        tier = "excluded"
        pool = "excluded_pool"
        selection_reason = "off-domain contract patterns strongly matched"
        exclusion_reason = "off_domain_blocked"
    elif neg >= 0.6 or len(neg_hits) >= 2:
        tier = "excluded"
        pool = "excluded_pool"
        selection_reason = "negative contexts strongly matched"
        exclusion_reason = "negative_context_blocked"
    elif metric_only and obj < 0.4 and mech < 0.4:
        tier = "excluded"
        pool = "excluded_pool"
        selection_reason = "metric-only signal without object/mechanism support"
        exclusion_reason = "metric_only_blocked"
    elif core_ok and final_score >= 0.6:
        tier = "core"
        pool = "core_pool"
        selection_reason = "strong core relevance"
    elif obj >= 0.4 and mech >= 0.4:
        tier = "mechanism_support"
        pool = "support_pool"
        selection_reason = "object/mechanism aligned but below core threshold"
        downrank_reason = "insufficient final score or context"
    elif eval_match >= 0.4 and metric >= 0.4:
        tier = "benchmark_or_evaluation"
        pool = "benchmark_pool"
        selection_reason = "evaluation-oriented evidence"
    elif mech >= 0.4 or metric >= 0.4:
        tier = "method_inspiration"
        pool = "inspiration_pool"
        selection_reason = "partial mechanism/metric inspiration"
    else:
        tier = "background"
        pool = "background_pool"
        selection_reason = "background context only"

    return {
        "candidate_id": str(c.get("candidate_id") or ""),
        "dedupe_key": str(c.get("dedupe_key") or ""),
        "title": str(c.get("title") or ""),
        "arxiv_id": c.get("arxiv_id"),
        "external_ids": c.get("external_ids") if isinstance(c.get("external_ids"), dict) else {},
        "source_url": c.get("source_url"),
        "landing_page_url": c.get("landing_page_url"),
        "abstract": c.get("abstract"),
        "source_name": c.get("source_name"),
        "final_score": max(0.0, min(1.0, final_score)),
        "tier": tier,
        "pool": pool,
        "selection_reason": selection_reason,
        "downrank_reason": downrank_reason,
        "exclusion_reason": exclusion_reason,
        "core_ok": core_ok,
        "score_breakdown": b,
        "matched_facets": c.get("matched_facets") or {},
        "negative_hits": neg_hits,
        "provenance": c.get("provenance") if isinstance(c.get("provenance"), list) else [],
    }


def tier_candidates(input_data: dict[str, Any]) -> dict[str, Any]:
    candidate_scores = input_data.get("candidate_scores") or []

    source_tiers = [_classify_one(c) for c in candidate_scores]

    tier_counts = {k: 0 for k in TIER_ENUM}
    negative_blocked_count = 0
    metric_only_blocked_count = 0
    off_domain_blocked_count = 0
    degraded_input_count = 0
    risk_flags: list[str] = []
    if not candidate_scores:
        risk_flags.append("missing_candidate_scores")

    for idx, c in enumerate(candidate_scores):
        t = source_tiers[idx]["tier"]
        tier_counts[t] += 1

        if source_tiers[idx].get("exclusion_reason") == "negative_context_blocked":
            negative_blocked_count += 1
        if source_tiers[idx].get("exclusion_reason") == "metric_only_blocked":
            metric_only_blocked_count += 1
        if source_tiers[idx].get("exclusion_reason") == "off_domain_blocked":
            off_domain_blocked_count += 1

        fqf = c.get("facets_quality_flags") or {}
        if bool(fqf.get("degraded_scoring")) or bool(fqf.get("facets_incomplete")):
            degraded_input_count += 1

    if degraded_input_count > 0:
        risk_flags.append("contains_degraded_scoring_inputs")
    if negative_blocked_count > 0:
        risk_flags.append("negative_context_blocks_present")
    if metric_only_blocked_count > 0:
        risk_flags.append("metric_only_blocks_present")
    if off_domain_blocked_count > 0:
        risk_flags.append("off_domain_blocks_present")

    return {
        "source_tiers": source_tiers,
        "tiering_report": {
            "tier_counts": tier_counts,
            "negative_blocked_count": negative_blocked_count,
            "metric_only_blocked_count": metric_only_blocked_count,
            "off_domain_blocked_count": off_domain_blocked_count,
            "degraded_input_count": degraded_input_count,
            "risk_flags": risk_flags,
        },
    }

"""Adapter for topk-source-selection Skill.

Migrated from OpenTrajectory p025_topk_selector.py top-k selection parts only.
No scoring/tiering/persistence.
"""

from __future__ import annotations

from typing import Any


def _safe_float(v: Any) -> float:
    try:
        return 0.0 if v is None else float(v)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: Any) -> int:
    try:
        return 0 if v is None else int(v)
    except (TypeError, ValueError):
        return 0


def _identity_key(row: dict[str, Any]) -> str:
    return str(row.get("dedupe_key") or row.get("candidate_id") or "")


def _rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -_safe_float(row.get("final_score")),
        str(row.get("title") or "").strip().lower(),
        _identity_key(row),
        str(row.get("candidate_id") or ""),
    )


def _recommended_acquisition(row: dict[str, Any]) -> str:
    if str(row.get("source_backend") or "").strip().lower() == "deepxiv":
        return "deepxiv_json"
    for item in row.get("provenance") or []:
        if isinstance(item, dict) and str(item.get("source_name") or "").lower() == "deepxiv":
            return "deepxiv_json"
    return "pdf_or_metadata"


def _light_title_diversity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply deterministic light diversity after stable pre-ranking."""
    ranked = sorted((dict(row) for row in rows), key=_rank_key)
    seen_prefix: set[str] = set()
    for row in ranked:
        title = str(row.get("title") or "").strip().lower()
        prefix = " ".join(title.split()[:2])
        penalty = 0.005 if prefix and prefix in seen_prefix else 0.0
        if prefix:
            seen_prefix.add(prefix)
        row["_diversity_penalty"] = penalty
        row["final_score"] = max(0.0, min(1.0, round(_safe_float(row.get("final_score")) - penalty, 4)))
    return sorted(ranked, key=_rank_key)


def _dedupe_ranked(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    removed = 0
    for row in rows:
        key = _identity_key(row)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        out.append(row)
    return out, removed


def _as_selected_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": str(row.get("candidate_id") or ""),
        "dedupe_key": str(row.get("dedupe_key") or ""),
        "title": str(row.get("title") or ""),
        "arxiv_id": row.get("arxiv_id"),
        "external_ids": row.get("external_ids") if isinstance(row.get("external_ids"), dict) else {},
        "source_url": row.get("source_url"),
        "landing_page_url": row.get("landing_page_url"),
        "abstract": row.get("abstract"),
        "source_name": row.get("source_name"),
        "final_score": max(0.0, min(1.0, _safe_float(row.get("final_score")))),
        "tier": str(row.get("tier") or ""),
        "pool": str(row.get("pool") or ""),
        "selection_reason": row.get("selection_reason"),
        "provenance": row.get("provenance") if isinstance(row.get("provenance"), list) else [],
        "score_breakdown": row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {},
        "matched_facets": row.get("matched_facets") if isinstance(row.get("matched_facets"), dict) else {},
    }


def build_topk_selection(input_data: dict[str, Any]) -> dict[str, Any]:
    source_tiers = input_data.get("source_tiers")
    cfg = input_data.get("topk_config") or {}

    core_min = _safe_int(cfg.get("core_min") if isinstance(cfg, dict) else None) or 25
    dynamic_target = _safe_int(cfg.get("dynamic_target") if isinstance(cfg, dict) else None) or 35
    dynamic_cap = _safe_int(cfg.get("dynamic_cap") if isinstance(cfg, dict) else None) or 60
    score_min = _safe_float(cfg.get("score_min") if isinstance(cfg, dict) else None) or 0.45
    extended_score_min = _safe_float(cfg.get("extended_score_min") if isinstance(cfg, dict) else None) or 0.60
    dynamic_target = max(dynamic_target, core_min)
    dynamic_cap = max(dynamic_cap, dynamic_target)

    thresholds = {
        "core_min": core_min,
        "dynamic_target": dynamic_target,
        "dynamic_cap": dynamic_cap,
        "score_min": score_min,
        "extended_score_min": extended_score_min,
    }

    if not isinstance(source_tiers, list) or not source_tiers:
        return {
            "topk_selection": {
                "selected_for_p03": [],
                "extended_selected": [],
                "supporting_papers": [],
                "method_inspiration_papers": [],
                "excluded_examples": [],
                "selection_stats": {"selected_total": 0, "selected_for_p03_count": 0, "thresholds": thresholds},
                "p03_acquisition_plan": {
                    "total_selected": 0,
                    "deepxiv_priority": {"count": 0, "dedupe_keys": []},
                    "pdf_or_metadata": {"count": 0, "dedupe_keys": []},
                },
                "risk_summary": {"risk_flags": ["missing_source_tiers"]},
                "degraded": True,
                "degradation_reasons": ["missing_or_empty_source_tiers"],
            }
        }

    ranked, duplicate_tier_rows_removed = _dedupe_ranked(_light_title_diversity([x for x in source_tiers if isinstance(x, dict)]))
    core_pool = [x for x in ranked if x.get("tier") == "core" and _safe_float(x.get("final_score")) >= score_min]
    mechanism_pool = [x for x in ranked if x.get("tier") == "mechanism_support"]
    method_pool = [x for x in ranked if x.get("tier") == "method_inspiration"]
    benchmark_pool = [x for x in ranked if x.get("tier") == "benchmark_or_evaluation"]
    excluded_pool = [x for x in ranked if x.get("tier") == "excluded"]
    supplemental = sorted(mechanism_pool + method_pool + benchmark_pool, key=_rank_key)

    selected_for_p03 = list(core_pool[:dynamic_target])
    selected_keys = {_identity_key(x) for x in selected_for_p03}
    supplemental_used_for_core_min = False
    if len(selected_for_p03) < core_min:
        supplemental_used_for_core_min = True
        for row in supplemental:
            if len(selected_for_p03) >= core_min:
                break
            if _identity_key(row) in selected_keys:
                continue
            selected_for_p03.append(row)
            selected_keys.add(_identity_key(row))

    extended_selected: list[dict[str, Any]] = []
    for row in core_pool[dynamic_target:] + supplemental:
        if len(selected_for_p03) + len(extended_selected) >= dynamic_cap:
            break
        key = _identity_key(row)
        if key in selected_keys or _safe_float(row.get("final_score")) < extended_score_min:
            continue
        extended_selected.append(row)
        selected_keys.add(key)

    selected_for_p03 = [row for row in selected_for_p03 if str(row.get("tier")) != "excluded"]
    extended_selected = [row for row in extended_selected if str(row.get("tier")) != "excluded"]
    all_selected_rows = selected_for_p03 + extended_selected

    low_core_pool_count = len(core_pool) < core_min
    selected_below_dynamic_target = len(selected_for_p03) < dynamic_target
    risk_flags: list[str] = []
    degradation_reasons: list[str] = []
    if low_core_pool_count:
        risk_flags.append("low_core_pool_count")
    if supplemental_used_for_core_min:
        risk_flags.append("supplemental_pool_used_for_core_min")
        degradation_reasons.append("supplemental_pool_used_for_core_min")
    if selected_below_dynamic_target:
        risk_flags.append("selected_below_dynamic_target")
        degradation_reasons.append("insufficient_candidates_for_dynamic_target")
    if duplicate_tier_rows_removed:
        risk_flags.append("duplicate_tier_rows_removed")

    deepxiv_keys: list[str] = []
    pdf_or_meta_keys: list[str] = []
    for row in all_selected_rows:
        target = deepxiv_keys if _recommended_acquisition(row) == "deepxiv_json" else pdf_or_meta_keys
        target.append(str(row.get("dedupe_key") or ""))

    return {
        "topk_selection": {
            "selected_for_p03": [_as_selected_item(x) for x in selected_for_p03],
            "extended_selected": [_as_selected_item(x) for x in extended_selected],
            "supporting_papers": [_as_selected_item(x) for x in mechanism_pool],
            "method_inspiration_papers": [_as_selected_item(x) for x in method_pool],
            "excluded_examples": [
                {
                    "candidate_id": str(x.get("candidate_id") or ""),
                    "dedupe_key": str(x.get("dedupe_key") or ""),
                    "title": str(x.get("title") or ""),
                    "final_score": max(0.0, min(1.0, _safe_float(x.get("final_score")))),
                    "tier": str(x.get("tier") or ""),
                    "pool": str(x.get("pool") or ""),
                    "exclusion_reason": x.get("exclusion_reason"),
                }
                for x in excluded_pool[:20]
            ],
            "selection_stats": {
                "core_count": len(core_pool),
                "selected_total": len(all_selected_rows),
                "selected_for_p03_count": len(selected_for_p03),
                "extended_count": len(extended_selected),
                "duplicate_tier_rows_removed": duplicate_tier_rows_removed,
                "mechanism_support_count": len(mechanism_pool),
                "method_inspiration_count": len(method_pool),
                "benchmark_or_evaluation_count": len(benchmark_pool),
                "excluded_count": len(excluded_pool),
                "thresholds": thresholds,
            },
            "p03_acquisition_plan": {
                "total_selected": len(all_selected_rows),
                "deepxiv_priority": {"count": len(deepxiv_keys), "dedupe_keys": deepxiv_keys},
                "pdf_or_metadata": {"count": len(pdf_or_meta_keys), "dedupe_keys": pdf_or_meta_keys},
            },
            "risk_summary": {
                "risk_flags": risk_flags,
                "low_core_pool_count": low_core_pool_count,
                "selected_below_dynamic_target": selected_below_dynamic_target,
                "duplicate_tier_rows_removed": duplicate_tier_rows_removed,
            },
            "degraded": bool(degradation_reasons),
            "degradation_reasons": degradation_reasons,
        }
    }

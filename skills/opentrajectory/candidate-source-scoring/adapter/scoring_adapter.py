"""Deterministic candidate-level scoring for screened research sources.

The adapter consumes the current screening result directly and uses only the
research context required for scoring. It does not perform screening, tiering,
or top-k selection.
"""

from __future__ import annotations

import re
from typing import Any

_CONTEXT_DIMENSIONS = (
    "research_objects",
    "conditions",
    "results_or_metrics",
    "mechanisms",
    "comparison_dimensions",
    "critical_evidence_requirements",
    "negative_signals",
    "off_domain_signals",
)
_ELIGIBLE_SCREENING_DECISIONS = {"include", "maybe"}
_SCREENING_DECISIONS = _ELIGIBLE_SCREENING_DECISIONS | {"exclude"}
_ACCESS_SCORES = {
    "structured_fulltext_ready": 1.0,
    "pdf_ready": 0.8,
    "landing_page_only": 0.55,
    "metadata_only": 0.35,
    "blocked": 0.1,
    "unknown": 0.2,
}
_DEGRADED_ACCESS = {"landing_page_only", "metadata_only", "blocked", "unknown"}


def _normalize_terms(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    terms: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        term = item.strip().lower()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _term_hit(term: str, text_lower: str) -> bool:
    if not term or not text_lower:
        return False
    if any(char in term for char in (" ", "-", "_", "+", ".", "/", "|")):
        return term in text_lower
    try:
        return bool(re.search(r"\b" + re.escape(term) + r"\b", text_lower))
    except re.error:
        return term in text_lower


def _matched_terms(text_lower: str, terms: list[str]) -> list[str]:
    return [term for term in terms if _term_hit(term, text_lower)]


def _score_from_match_count(count: int) -> float:
    if count <= 0:
        return 0.0
    return min(1.0, 0.4 + 0.2 * count)


def _penalty_from_match_count(count: int) -> float:
    if count <= 0:
        return 0.0
    if count == 1:
        return 0.35
    if count == 2:
        return 0.6
    return 0.9


def _off_domain_penalty(count: int) -> float:
    if count <= 0:
        return 0.0
    if count == 1:
        return 0.4
    return 0.8


def _research_context_state(research_context: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    terms: dict[str, list[str]] = {}
    missing: list[str] = []
    for key in _CONTEXT_DIMENSIONS:
        if key not in research_context:
            missing.append(key)
            terms[key] = []
        else:
            terms[key] = _normalize_terms(research_context[key])
    return terms, missing


def _required_string(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"screened candidate requires string {key}")
    return value


def _required_nonempty_string(row: dict[str, Any], key: str) -> str:
    value = _required_string(row, key).strip()
    if not value:
        raise ValueError(f"screened candidate requires non-empty {key}")
    return value


def _required_list(row: dict[str, Any], key: str) -> list[Any]:
    value = row.get(key)
    if not isinstance(value, list):
        raise ValueError(f"screened candidate requires array {key}")
    return value


def _candidate_text(row: dict[str, Any]) -> tuple[str, str]:
    title = _required_string(row, "title").strip()
    abstract_value = row.get("abstract")
    if abstract_value is not None and not isinstance(abstract_value, str):
        raise ValueError("screened candidate abstract must be string or null when present")
    abstract = abstract_value.strip() if isinstance(abstract_value, str) else ""
    scientific_text = f"{title}\n{abstract}".strip().lower()
    relevance_signals = _normalize_terms(_required_list(row, "relevance_signals"))
    scoring_text = "\n".join([scientific_text, *relevance_signals]).strip()
    return scientific_text, scoring_text


def _source_accessibility(access_status: str) -> float:
    if access_status not in _ACCESS_SCORES:
        raise ValueError(f"unsupported access_status: {access_status}")
    return _ACCESS_SCORES[access_status]


def _screening_relevance(decision: str) -> float:
    if decision == "include":
        return 1.0
    if decision == "maybe":
        return 0.55
    raise ValueError(f"screening relevance is undefined for decision: {decision}")


def _evidence_potential(row: dict[str, Any], access_status: str) -> float:
    abstract_value = row.get("abstract")
    abstract = abstract_value.strip() if isinstance(abstract_value, str) else ""
    relevance_signals = _normalize_terms(_required_list(row, "relevance_signals"))
    score = 0.65 if abstract else 0.25
    score += min(0.2, 0.05 * len(relevance_signals))
    if access_status in {"structured_fulltext_ready", "pdf_ready"}:
        score += 0.1
    return min(1.0, score)


def _critical_coverage(scoring_text: str, requirements: list[str]) -> tuple[list[str], list[str], float]:
    if not requirements:
        return [], [], 0.0
    matched = _matched_terms(scoring_text, requirements)
    unmatched = [term for term in requirements if term not in matched]
    coverage_ratio = len(matched) / len(requirements)
    return matched, unmatched, round(0.15 * coverage_ratio, 6)


def _final_score(parts: dict[str, float]) -> float:
    total = (
        0.08 * parts["domain_match"]
        + 0.20 * parts["object_match"]
        + 0.14 * parts["mechanism_match"]
        + 0.14 * parts["metric_match"]
        + 0.12 * parts["context_match"]
        + 0.08 * parts["evaluation_match"]
        + 0.08 * parts["screening_relevance"]
        + 0.08 * parts["evidence_potential"]
        + 0.08 * parts["source_accessibility"]
        + parts["slot_coverage_bonus"]
        - 0.25 * parts["negative_penalty"]
        - 0.20 * parts["off_domain_penalty"]
    )
    return max(0.0, min(1.0, total))


def score_candidates(input_data: dict[str, Any]) -> dict[str, Any]:
    screened_candidates = input_data.get("screened_candidates")
    research_context = input_data.get("research_context")
    if not isinstance(screened_candidates, list):
        raise ValueError("screened_candidates must be an array")
    if not isinstance(research_context, dict):
        raise ValueError("research_context must be an object")

    research_question = research_context.get("research_question")
    if not isinstance(research_question, str) or not research_question.strip():
        raise ValueError("research_context.research_question must be a non-empty string")

    context_terms, missing_context_dimensions = _research_context_state(research_context)
    context_incomplete = bool(missing_context_dimensions)
    domain_terms = list(
        dict.fromkeys(
            context_terms["research_objects"]
            + context_terms["conditions"]
            + context_terms["results_or_metrics"]
            + context_terms["mechanisms"]
        )
    )

    output_rows: list[dict[str, Any]] = []
    unscored_candidates: list[dict[str, str]] = []
    screening_excluded_count = 0
    screening_eligible_count = 0
    missing_abstract_count = 0
    access_degraded_count = 0
    critical_coverage_degraded_count = 0
    off_domain_candidate_count = 0

    for row in screened_candidates:
        if not isinstance(row, dict):
            raise ValueError("each screened candidate must be an object")

        candidate_id = _required_string(row, "candidate_id")
        decision = _required_nonempty_string(row, "screening_decision").lower()
        if decision not in _SCREENING_DECISIONS:
            raise ValueError(f"unsupported screening_decision: {decision}")
        _required_string(row, "screening_reason")
        _required_list(row, "relevance_signals")
        provenance = _required_list(row, "provenance")
        access_status = _required_nonempty_string(row, "access_status").lower()
        _source_accessibility(access_status)

        if decision == "exclude":
            screening_excluded_count += 1
            continue

        screening_eligible_count += 1
        dedupe_key = row.get("dedupe_key")
        if not isinstance(dedupe_key, str) or not dedupe_key.strip():
            unscored_candidates.append({"candidate_id": candidate_id, "reason": "missing_dedupe_key"})
            continue
        dedupe_key = dedupe_key.strip()

        title = _required_string(row, "title").strip()
        abstract_value = row.get("abstract")
        abstract = abstract_value.strip() if isinstance(abstract_value, str) else ""
        scientific_text, scoring_text = _candidate_text(row)

        object_hits = _matched_terms(scoring_text, context_terms["research_objects"])
        mechanism_hits = _matched_terms(scoring_text, context_terms["mechanisms"])
        metric_hits = _matched_terms(scoring_text, context_terms["results_or_metrics"])
        condition_hits = _matched_terms(scoring_text, context_terms["conditions"])
        comparison_hits = _matched_terms(scoring_text, context_terms["comparison_dimensions"])
        domain_hits = _matched_terms(scoring_text, domain_terms)
        negative_hits = _matched_terms(scientific_text, context_terms["negative_signals"])
        off_domain_hits = _matched_terms(scientific_text, context_terms["off_domain_signals"])
        critical_hits, unmatched_critical, coverage_bonus = _critical_coverage(
            scoring_text, context_terms["critical_evidence_requirements"]
        )

        breakdown = {
            "domain_match": _score_from_match_count(len(domain_hits)),
            "object_match": _score_from_match_count(len(object_hits)),
            "mechanism_match": _score_from_match_count(len(mechanism_hits)),
            "metric_match": _score_from_match_count(len(metric_hits)),
            "context_match": _score_from_match_count(len(condition_hits)),
            "evaluation_match": _score_from_match_count(len(comparison_hits)),
            "screening_relevance": _screening_relevance(decision),
            "evidence_potential": _evidence_potential(row, access_status),
            "source_accessibility": _source_accessibility(access_status),
            "diversity_bonus": 0.0,
            "negative_penalty": _penalty_from_match_count(len(negative_hits)),
            "off_domain_penalty": _off_domain_penalty(len(off_domain_hits)),
            "slot_coverage_bonus": coverage_bonus,
        }
        final_score = _final_score(breakdown)

        degradation_reasons: list[str] = []
        if context_incomplete:
            degradation_reasons.append("research_context_incomplete")
        if not abstract:
            missing_abstract_count += 1
            degradation_reasons.append("missing_abstract")
        if access_status in _DEGRADED_ACCESS:
            access_degraded_count += 1
            degradation_reasons.append(f"access_degraded:{access_status}")
        if decision == "maybe":
            degradation_reasons.append("screening_maybe")
        if unmatched_critical:
            critical_coverage_degraded_count += 1
            degradation_reasons.append("critical_evidence_requirements_incomplete")
        if breakdown["off_domain_penalty"] >= 0.6:
            off_domain_candidate_count += 1

        output_row: dict[str, Any] = {
            "candidate_id": candidate_id,
            "dedupe_key": dedupe_key,
            "title": title,
            "provenance": provenance,
            "screening_decision": decision,
            "screening_reason": row["screening_reason"],
            "relevance_signals": row["relevance_signals"],
            "access_status": access_status,
            "degradation_reasons": degradation_reasons,
            "final_score": round(final_score, 4),
            "score_breakdown": {key: round(value, 4) for key, value in breakdown.items()},
            "matched_facets": {
                "object": object_hits,
                "mechanism": mechanism_hits,
                "metric": metric_hits,
                "context": condition_hits,
                "evaluation": comparison_hits,
                "must_have": critical_hits,
                "contract_slots": critical_hits,
                "required_slots": critical_hits,
                "off_domain": off_domain_hits,
            },
            "negative_hits": negative_hits,
            "must_have_hit_count": len(critical_hits),
            "metric_only": (
                breakdown["metric_match"] >= 0.4
                and breakdown["object_match"] < 0.4
                and breakdown["mechanism_match"] < 0.4
                and breakdown["context_match"] < 0.4
            ),
            "facets_quality_flags": {
                "facets_incomplete": context_incomplete,
                "missing_facets": missing_context_dimensions,
                "degraded_scoring": bool(degradation_reasons),
            },
        }

        source_name = row.get("source_name")
        if isinstance(source_name, str) and source_name.strip():
            output_row["source_name"] = source_name.strip()

        for key in (
            "abstract",
            "external_ids",
            "source_url",
            "landing_page_url",
            "pdf_url",
            "year",
            "citation_count",
        ):
            if key in row:
                output_row[key] = row[key]

        output_rows.append(output_row)

    output_rows.sort(key=lambda item: (-item["final_score"], item["dedupe_key"], item["candidate_id"]))

    risk_flags: list[str] = []
    if context_incomplete:
        risk_flags.append("research_context_incomplete")
    if missing_abstract_count:
        risk_flags.append("missing_abstract_present")
    if access_degraded_count:
        risk_flags.append("access_degraded_present")
    if critical_coverage_degraded_count:
        risk_flags.append("critical_evidence_coverage_incomplete")
    if off_domain_candidate_count:
        risk_flags.append("off_domain_candidates_detected")
    if unscored_candidates:
        risk_flags.append("unscored_missing_identity")

    return {
        "candidate_scores": output_rows,
        "scoring_report": {
            "input_count": len(screened_candidates),
            "screening_eligible_count": screening_eligible_count,
            "screening_excluded_count": screening_excluded_count,
            "unscored_identity_count": len(unscored_candidates),
            "output_count": len(output_rows),
            "facets_incomplete": context_incomplete,
            "missing_facets": missing_context_dimensions,
            "degraded_scoring": bool(risk_flags),
            "off_domain_candidate_count": off_domain_candidate_count,
            "missing_abstract_count": missing_abstract_count,
            "access_degraded_count": access_degraded_count,
            "critical_coverage_degraded_count": critical_coverage_degraded_count,
            "unscored_candidates": unscored_candidates,
            "risk_flags": risk_flags,
        },
    }

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from evals.opentrajectory.harness.common.deterministic_checks import (
    _result,
    aggregate_deterministic_results,
)


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_outer_parens(text: str) -> str:
    s = text.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        ok = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    ok = False
                    break
        if ok and depth == 0:
            s = s[1:-1].strip()
        else:
            break
    return s


def _normalize_query(query: str) -> str:
    s = _norm_space(query.lower())
    s = re.sub(r"\s*\b(and|or|not)\b\s*", lambda m: f" {m.group(1)} ", s)
    s = _norm_space(s)
    s = _strip_outer_parens(s)
    return s


def _phase3_payload(context: dict[str, Any]) -> dict[str, Any]:
    payload = context.get("output_payload")
    if isinstance(payload, dict):
        return payload
    nested = context.get("output")
    if isinstance(nested, dict) and isinstance(nested.get("output_payload"), dict):
        return nested["output_payload"]
    return context


def _all_queries(context: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _phase3_payload(context)
    return list(payload.get("expanded_query_variants") or []) + list(
        payload.get("search_matrix") or []
    )


def check_p3_001(context: dict[str, Any]):
    payload = _phase3_payload(context)
    if context.get("execution_mode") != "executed":
        return _result("P3-001", "not_applicable", "hard_gate", "execution_mode is not executed")
    queries = payload.get("expanded_query_variants") or []
    if len(queries) >= 1:
        return _result(
            "P3-001",
            "passed",
            "hard_gate",
            "at least one query exists",
            {"query_count": len(queries)},
        )
    return _result("P3-001", "failed", "hard_gate", "query collection is empty", {"query_count": 0})


def check_p3_002(context: dict[str, Any]):
    payload = _phase3_payload(context)
    if context.get("execution_mode") != "executed":
        return _result("P3-002", "not_applicable", "hard_gate", "execution_mode is not executed")
    for idx, item in enumerate(payload.get("expanded_query_variants") or []):
        q = str(item.get("query", "") if isinstance(item, dict) else item).strip()
        if not q:
            return _result(
                "P3-002", "failed", "hard_gate", "query is empty after trim", {"index": idx}
            )
    return _result(
        "P3-002",
        "passed",
        "hard_gate",
        "all query strings are nonempty after trim; "
        "syntax and scientific quality are not evaluated",
        {},
    )


def check_p3_003(context: dict[str, Any]):
    payload = _phase3_payload(context)
    seen: dict[str, list[int]] = defaultdict(list)
    for idx, item in enumerate(payload.get("expanded_query_variants") or []):
        norm = _normalize_query(str(item.get("query", "")))
        seen[norm].append(idx)
    dupes = [q for q, idxs in seen.items() if len(idxs) > 1]
    if dupes:
        return _result(
            "P3-003",
            "failed",
            "score_penalty",
            "duplicate queries detected",
            {"duplicate_normalized_queries": dupes},
        )
    return _result("P3-003", "passed", "score_penalty", "no exact normalized duplicates", {})


def check_p3_008(context: dict[str, Any]):
    payload = _phase3_payload(context)
    constraints = list(
        payload.get("required_source_constraints")
        or context.get("required_source_constraints")
        or []
    )
    if not constraints:
        return _result("P3-008", "not_applicable", "hard_gate", "required_source_constraints empty")
    utilized = set(
        (payload.get("quality_report") or context.get("quality_report") or {}).get(
            "constraints_utilized"
        )
        or []
    )
    missing = [c for c in constraints if c not in utilized]
    if missing:
        return _result(
            "P3-008",
            "failed",
            "hard_gate",
            "required constraint identifiers missing",
            {"missing_constraints": missing},
        )
    return _result("P3-008", "passed", "hard_gate", "required constraint identifiers declared", {})


def check_p3_009(context: dict[str, Any]):
    payload = _phase3_payload(context)
    unconfirmed = list(payload.get("unconfirmed_items") or context.get("unconfirmed_items") or [])
    if not unconfirmed:
        return _result("P3-009", "not_applicable", "hard_gate", "unconfirmed_items empty")
    strict_terms = [
        _normalize_query(t)
        for t in (payload.get("strict_terms") or context.get("strict_terms") or [])
    ]
    negative_terms = [
        _normalize_query(t)
        for t in (payload.get("negative_terms") or context.get("negative_terms") or [])
    ]
    leaked = []
    for item in unconfirmed:
        norm = _normalize_query(str(item))
        if any(term == norm for term in strict_terms) or any(
            term == norm for term in negative_terms
        ):
            leaked.append(item)
    if leaked:
        return _result(
            "P3-009",
            "failed",
            "hard_gate",
            "unconfirmed items leaked into strict or negative terms",
            {"leaked_items": leaked},
        )
    return _result(
        "P3-009",
        "passed",
        "hard_gate",
        "unconfirmed items are not promoted into strict or negative terms",
        {},
    )


def check_p3_010(context: dict[str, Any]):
    payload = _phase3_payload(context)
    if not (payload.get("fallback_used") or context.get("fallback_used")):
        return _result("P3-010", "not_applicable", "hard_gate", "fallback_used is false")
    if (payload.get("overall_status") or context.get("overall_status")) == "passed" or context.get(
        "baseline_eligible"
    ) is True:
        return _result("P3-010", "failed", "hard_gate", "fallback cannot masquerade as success", {})
    return _result("P3-010", "passed", "hard_gate", "fallback is not treated as pass", {})


def check_p3_011(context: dict[str, Any]):
    if context.get("handling_class") != "conservative_generate":
        return _result(
            "P3-011",
            "not_applicable",
            "manual_review_trigger",
            "handling_class is not conservative_generate",
        )
    return _result(
        "P3-011",
        "manual_review_trigger",
        "manual_review_trigger",
        "conservative_generate requires manual review",
        {},
    )


def check_p3_012(context: dict[str, Any]):
    if context.get("handling_class") != "manual_review_required":
        return _result(
            "P3-012",
            "not_applicable",
            "manual_review_trigger",
            "handling_class is not manual_review_required",
        )
    return _result(
        "P3-012",
        "manual_review_trigger",
        "manual_review_trigger",
        "manual_review_required requires manual review",
        {},
    )


def check_p3_013(context: dict[str, Any]):
    if context.get("handling_class") != "reject_unreliable_input":
        return _result(
            "P3-013", "not_applicable", "hard_gate", "handling_class is not reject_unreliable_input"
        )
    if context.get("overall_status") == "passed" or context.get("baseline_eligible") is True:
        return _result(
            "P3-013",
            "failed",
            "hard_gate",
            "reject_unreliable_input must not be treated as success",
            {},
        )
    return _result(
        "P3-013", "passed", "hard_gate", "reject_unreliable_input is rejected from baseline", {}
    )


PHASE3_CHECKS = [
    check_p3_001,
    check_p3_002,
    check_p3_003,
    check_p3_008,
    check_p3_009,
    check_p3_010,
    check_p3_011,
    check_p3_012,
    check_p3_013,
]


def run_phase3_deterministic_checks(context: dict[str, Any]) -> dict[str, Any]:
    checks = [fn(context) for fn in PHASE3_CHECKS]
    baseline = context.get("baseline_eligible")
    result = aggregate_deterministic_results(checks, baseline_eligible=baseline)
    result.update(
        assessment_scope="structural_contract",
        scientific_quality_status="not_evaluated",
        baseline_eligible=False,
    )
    return result

"""Self-contained multi-source metadata retrieval orchestration.

P0.7a rules:
- OpenAlex and arXiv execute from this Skill package without legacy 04_backend imports.
- Semantic Scholar is available as an optional best-effort metadata source.
- PDF bytes are never downloaded.
- One source may fail without discarding candidates from healthy sources when
  allow_partial_success=True.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from pathlib import Path
from typing import Any

from .metadata_mapper import arxiv_hit_to_candidate, lit_candidate_to_candidate
from .query_adapter import adapt_query_for_source
from .standalone_sources import search_arxiv, search_openalex, search_semantic_scholar
from typing import Callable


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿㐀-䶿豈-﫿぀-ヿ가-힯]", text))


def _fetch_arxiv(
    adapted_query: str,
    original_query: str,
    q_idx: int,
    search_intent: str | None,
    variant_type: str | None,
    retrieval_mode: str,
    max_results: int,
    timeout_s: int,
    decision_evidence_slot: str | None = None,
    slot_reason: str | None = None,
    targeted_from_missing_item: bool | None = None,
    retrieval_intent: str | None = None,
    slot_ids: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    try:
        hits = search_arxiv(adapted_query, max_results=max_results, timeout_s=timeout_s)
        return [
            arxiv_hit_to_candidate(
                hit,
                original_query,
                q_idx,
                r_idx,
                search_intent,
                variant_type,
                retrieval_mode=retrieval_mode,
                timeout_s=timeout_s,
                decision_evidence_slot=decision_evidence_slot,
                slot_reason=slot_reason,
                targeted_from_missing_item=targeted_from_missing_item,
                retrieval_intent=retrieval_intent,
                slot_ids=slot_ids,
            )
            for r_idx, hit in enumerate(hits)
        ], None
    except Exception as exc:  # source boundary: preserve partial-success behavior
        return [], _classify_error("arxiv", exc)


def _fetch_openalex(
    adapted_query: str,
    original_query: str,
    q_idx: int,
    search_intent: str | None,
    variant_type: str | None,
    retrieval_mode: str,
    max_results: int,
    timeout_s: int,
    decision_evidence_slot: str | None = None,
    slot_reason: str | None = None,
    targeted_from_missing_item: bool | None = None,
    retrieval_intent: str | None = None,
    slot_ids: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    try:
        results = search_openalex(adapted_query, max_results=max_results, timeout_s=timeout_s)
        candidates = []
        for r_idx, lit in enumerate(results):
            openalex_id = lit.dedupe_key[len("openalex:"):] if lit.dedupe_key.startswith("openalex:") else None
            candidates.append(lit_candidate_to_candidate(
                lit,
                "openalex",
                original_query,
                q_idx,
                r_idx,
                search_intent,
                variant_type,
                retrieval_mode=retrieval_mode,
                openalex_id=openalex_id,
                decision_evidence_slot=decision_evidence_slot,
                slot_reason=slot_reason,
                targeted_from_missing_item=targeted_from_missing_item,
                retrieval_intent=retrieval_intent,
                slot_ids=slot_ids,
            ))
        return candidates, None
    except Exception as exc:
        return [], _classify_error("openalex", exc)


def _fetch_semantic_scholar(
    adapted_query: str,
    original_query: str,
    q_idx: int,
    search_intent: str | None,
    variant_type: str | None,
    retrieval_mode: str,
    max_results: int,
    timeout_s: int,
    api_key: str | None = None,
    decision_evidence_slot: str | None = None,
    slot_reason: str | None = None,
    targeted_from_missing_item: bool | None = None,
    retrieval_intent: str | None = None,
    slot_ids: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    try:
        results = search_semantic_scholar(
            adapted_query,
            api_key=api_key,
            max_results=max_results,
            timeout_s=timeout_s,
            require_pdf=False,
        )
        candidates = []
        for r_idx, lit in enumerate(results):
            s2_id = lit.dedupe_key[3:] if lit.dedupe_key.startswith("s2:") else None
            candidates.append(lit_candidate_to_candidate(
                lit,
                "semantic_scholar",
                original_query,
                q_idx,
                r_idx,
                search_intent,
                variant_type,
                retrieval_mode=retrieval_mode,
                semantic_scholar_id=s2_id,
                decision_evidence_slot=decision_evidence_slot,
                slot_reason=slot_reason,
                targeted_from_missing_item=targeted_from_missing_item,
                retrieval_intent=retrieval_intent,
                slot_ids=slot_ids,
            ))
        return candidates, None
    except Exception as exc:
        return [], _classify_error("semantic_scholar", exc)


def _fetch_deepxiv(
    adapted_query: str,
    original_query: str,
    q_idx: int,
    search_intent: str | None,
    variant_type: str | None,
    retrieval_mode: str,
    max_results: int,
    timeout_s: int,
    deepxiv_token: str | None = None,
    decision_evidence_slot: str | None = None,
    slot_reason: str | None = None,
    targeted_from_missing_item: bool | None = None,
    retrieval_intent: str | None = None,
    slot_ids: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    del adapted_query, original_query, q_idx, search_intent, variant_type, retrieval_mode, max_results, timeout_s, deepxiv_token
    del decision_evidence_slot, slot_reason, targeted_from_missing_item, retrieval_intent, slot_ids
    return [], "deepxiv is not enabled in the self-contained P0.7a runtime slice"


def _classify_error(source: str, exc: Exception) -> str:
    msg = str(exc)
    if "429" in msg:
        return f"429 rate limit (no API key or quota exceeded): {msg}"
    if "SSL" in msg or "ssl" in msg or "certificate" in msg.lower():
        return f"SSL error: {msg}"
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return f"timeout: {msg}"
    return f"{source}: {msg}"


_SOURCE_FETCHERS = {
    "arxiv": _fetch_arxiv,
    "openalex": _fetch_openalex,
    "semantic_scholar": _fetch_semantic_scholar,
    "deepxiv": _fetch_deepxiv,
}


def _plain_matrix_query(row: dict[str, Any]) -> str:
    terms: list[str] = []
    for field in ("must_include_terms", "query_terms", "optional_terms"):
        for value in row.get(field) or []:
            if isinstance(value, str) and value.strip() and not _has_cjk(value):
                terms.append(value.strip())
    return " ".join(dict.fromkeys(terms))[:400]


def _select_queries(skill2_data: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, list[str]]:
    base_selected: list[dict[str, Any]] = []
    targeted: list[dict[str, Any]] = []
    risk_flags: list[str] = []

    for variant in skill2_data.get("expanded_query_variants") or []:
        query = variant.get("query") if isinstance(variant, dict) else None
        if isinstance(query, str) and query.strip() and not _has_cjk(query):
            normalized = {**variant, "query": query.strip()}
            if "retrieval_mode" not in normalized:
                normalized["retrieval_mode"] = "expanded_query_variant"
            if variant.get("targeted_from_missing_item"):
                targeted.append(normalized)
            else:
                base_selected.append(normalized)

    selected = base_selected + targeted
    if selected:
        return selected, False, risk_flags

    risk_flags.append("missing_english_query_variants")
    for row in skill2_data.get("search_matrix") or []:
        if not isinstance(row, dict):
            continue
        query = _plain_matrix_query(row)
        if query:
            selected.append({
                "query": query,
                "search_intent": row.get("search_intent"),
                "variant_type": "fallback",
                "retrieval_mode": "search_matrix_fallback",
            })

    if selected:
        return selected, True, risk_flags

    user_query = skill2_data.get("user_query")
    if isinstance(user_query, str) and user_query.strip() and not _has_cjk(user_query):
        selected.append({
            "query": user_query.strip(),
            "search_intent": "user_query_fallback",
            "variant_type": "fallback",
            "retrieval_mode": "english_user_query_fallback",
        })
        return selected, True, risk_flags

    risk_flags.append("chinese_user_query_not_used_as_search_query")
    return [], True, risk_flags


def _fetch_for_variant_source(
    *,
    variant: dict[str, Any],
    q_idx: int,
    source: str,
    max_results_per_query: int,
    timeout_seconds: int,
    s2_api_key: str | None,
    deepxiv_token: str | None,
) -> tuple[list[dict], str | None, str | None]:
    original_query = variant["query"]
    variant_type = variant.get("variant_type")
    adapted = adapt_query_for_source(original_query, source, variant_type)
    if adapted.warning and adapted.warning.startswith("skipped"):
        return [], None, f"[{source}] q{q_idx}: {adapted.warning}"
    warning = f"[{source}] q{q_idx}: {adapted.warning}" if adapted.warning else None

    fetcher = _SOURCE_FETCHERS.get(source)
    if fetcher is None:
        error = f"source is not enabled in the self-contained P0.7a runtime: {source}"
        return [], error, warning

    search_intent = variant.get("search_intent")
    retrieval_mode = variant.get("retrieval_mode") or "expanded_query_variant"
    decision_evidence_slot = variant.get("decision_evidence_slot")
    slot_reason = variant.get("slot_reason")
    targeted_from_missing_item = variant.get("targeted_from_missing_item")
    retrieval_intent = variant.get("retrieval_intent")
    slot_ids = variant.get("slot_ids")

    if source == "semantic_scholar":
        new_candidates, error = fetcher(
            adapted.query, original_query, q_idx, search_intent, variant_type,
            retrieval_mode, max_results_per_query, timeout_seconds, s2_api_key,
            decision_evidence_slot=decision_evidence_slot,
            slot_reason=slot_reason,
            targeted_from_missing_item=targeted_from_missing_item,
            retrieval_intent=retrieval_intent,
            slot_ids=slot_ids,
        )
    elif source == "deepxiv":
        new_candidates, error = fetcher(
            adapted.query, original_query, q_idx, search_intent, variant_type,
            retrieval_mode, max_results_per_query, timeout_seconds, deepxiv_token,
            decision_evidence_slot=decision_evidence_slot,
            slot_reason=slot_reason,
            targeted_from_missing_item=targeted_from_missing_item,
            retrieval_intent=retrieval_intent,
            slot_ids=slot_ids,
        )
    else:
        new_candidates, error = fetcher(
            adapted.query, original_query, q_idx, search_intent, variant_type,
            retrieval_mode, max_results_per_query, timeout_seconds,
            decision_evidence_slot=decision_evidence_slot,
            slot_reason=slot_reason,
            targeted_from_missing_item=targeted_from_missing_item,
            retrieval_intent=retrieval_intent,
            slot_ids=slot_ids,
        )
    return new_candidates, error, warning


def run_retrieval(
    skill2_data: dict,
    target_sources: list[str],
    max_results_per_query: int,
    max_total_candidates: int,
    timeout_seconds: int,
    allow_partial_success: bool,
    ot_backend_path: Path | None = None,
    s2_api_key: str | None = None,
    deepxiv_token: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict:
    """Retrieve the executable retrieval plan without scientific screening.

    Query/source requests run with bounded parallelism. Runtime limits only
    protect execution resources; they are never treated as scientific
    completeness. Results are merged in stable query/source order so
    concurrency does not change output order.
    """
    del ot_backend_path
    selected, fallback_used, risk_flags = _select_queries(skill2_data)
    candidate_pool_limit = max(0, int(max_total_candidates))
    source_counts: dict[str, int] = {source: 0 for source in target_sources}
    query_counts: dict[str, int] = {}
    source_had_success: dict[str, bool] = {source: False for source in target_sources}
    source_errors: dict[str, str] = {}

    # Missing-item queries directly answer a known research gap, so execute them
    # before ordinary expansion queries when the global candidate budget is tight.
    ordered_variants = [
        *[row for row in selected if row.get("targeted_from_missing_item")],
        *[row for row in selected if not row.get("targeted_from_missing_item")],
    ]
    fetch_plan = [
        (q_idx, variant, source)
        for q_idx, variant in enumerate(ordered_variants)
        for source in target_sources
    ]
    fetch_results: dict[int, tuple[list[dict], str | None, str | None]] = {}

    if fetch_plan:
        worker_count = min(4, len(fetch_plan))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="ot-source-retrieval",
        ) as executor:
            futures = {
                executor.submit(
                    _fetch_for_variant_source,
                    variant=variant,
                    q_idx=q_idx,
                    source=source,
                    max_results_per_query=max_results_per_query,
                    timeout_seconds=timeout_seconds,
                    s2_api_key=s2_api_key,
                    deepxiv_token=deepxiv_token,
                ): index
                for index, (q_idx, variant, source) in enumerate(fetch_plan)
            }
            source_totals: dict[str, int] = {source: 0 for source in target_sources}
            for future in as_completed(futures):
                index = futures[future]
                q_idx, _variant, source = fetch_plan[index]
                try:
                    result = future.result()
                except Exception as exc:
                    result = ([], _classify_error(source, exc), None)
                fetch_results[index] = result

                batch_candidates, batch_error, _batch_warning = result
                if batch_error is None:
                    source_totals[source] = source_totals.get(source, 0) + len(batch_candidates)
                if progress_callback is not None:
                    try:
                        progress_callback({
                            "phase": "source_batch_completed",
                            "source_name": source,
                            "query_index": q_idx,
                            "batch_candidate_count": len(batch_candidates),
                            "source_candidate_count": source_totals.get(source, 0),
                            "failed": batch_error is not None,
                            "sample_candidates": [
                                {
                                    "title": str(item.get("title") or "").strip(),
                                    "doi": str(item.get("doi") or "").strip(),
                                    "url": str(
                                        item.get("source_url")
                                        or item.get("landing_page_url")
                                        or item.get("pdf_url")
                                        or item.get("url")
                                        or ""
                                    ).strip(),
                                }
                                for item in batch_candidates[:3]
                                if isinstance(item, dict)
                            ],
                        })
                    except Exception:
                        # User-facing progress is best-effort and never changes
                        # the scientific retrieval result.
                        pass

    candidates: list[dict] = []
    candidate_limit_reached = False
    for index, (q_idx, variant, source) in enumerate(fetch_plan):
        new_candidates, error, warning = fetch_results[index]
        if warning:
            risk_flags.append(warning)
        if error is not None:
            source_errors[source] = error
            risk_flags.append(f"[{source}] q{q_idx} error: {error}")
            continue

        source_had_success[source] = True
        source_counts[source] = source_counts.get(source, 0) + len(new_candidates)
        query = str(variant.get("query") or "")
        query_counts[query[:80]] = query_counts.get(query[:80], 0) + len(new_candidates)

        remaining = candidate_pool_limit - len(candidates)
        if remaining <= 0:
            if new_candidates:
                candidate_limit_reached = True
            continue
        if len(new_candidates) > remaining:
            candidate_limit_reached = True
        candidates.extend(new_candidates[:remaining])

    if candidate_limit_reached:
        risk_flags.append("candidate_limit_reached")

    failed_sources = [
        {"source_name": source, "error": source_errors[source]}
        for source in target_sources
        if not source_had_success[source] and source in source_errors
    ]
    partial_success = bool(source_errors)
    if failed_sources and not allow_partial_success:
        raise RuntimeError(
            "retrieval source failure with allow_partial_success=false: "
            f"{failed_sources}"
        )

    return {
        "metadata_candidates": candidates,
        "retrieval_report": {
            "source_counts": source_counts,
            "query_counts": query_counts,
            "failed_sources": failed_sources,
            "total_candidates": len(candidates),
            "partial_success": partial_success,
            "fallback_used": fallback_used,
            "risk_flags": risk_flags,
        },
    }

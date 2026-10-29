"""Adapter for candidate-dedup-and-provenance Skill.

Migrated from OpenTrajectory literature_merge_filter.py merge_candidates().
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


def _canonicalize_dedupe_key(raw: Any) -> str:
    """Normalize equivalent external identifiers to one stable key format."""
    value = str(raw or "").strip().lower()
    if not value:
        return ""

    if value.startswith("doi:"):
        suffix = value[4:].strip()
        return f"doi:{suffix}" if suffix else ""
    if value.startswith("https://doi.org/"):
        return f"doi:{value.removeprefix('https://doi.org/').strip()}"
    if value.startswith("http://doi.org/"):
        return f"doi:{value.removeprefix('http://doi.org/').strip()}"
    if value.startswith("10."):
        return f"doi:{value}"

    if value.startswith("arxiv:"):
        suffix = value[6:].strip()
        return f"arxiv:{re.sub(r'v\d+$', '', suffix)}" if suffix else ""

    if value.startswith("title_hash:"):
        return f"title:{value.removeprefix('title_hash:')}"
    if value.startswith("normalized_title_hash_"):
        return f"title:{value.removeprefix('normalized_title_hash_')}"

    return value


def _compute_dedupe_key(candidate: dict[str, Any]) -> str:
    """Compute a stable key while preserving cross-source title-key compatibility."""
    external_ids = candidate.get("external_ids") or {}
    if not isinstance(external_ids, dict):
        external_ids = {}

    # Always prefer strong identifiers exposed by metadata. A legacy upstream
    # key may have been computed before a DOI was discovered by another source.
    doi = _canonicalize_dedupe_key(external_ids.get("doi") or candidate.get("doi"))
    if doi:
        return doi

    existing_key = _canonicalize_dedupe_key(candidate.get("dedupe_key"))
    if existing_key.startswith("doi:"):
        return existing_key

    arxiv_id = str(external_ids.get("arxiv_id") or candidate.get("arxiv_id") or "").strip().lower()
    if arxiv_id:
        return f"arxiv:{re.sub(r'v\d+$', '', arxiv_id)}"
    if existing_key.startswith("arxiv:"):
        return existing_key

    # Keep an upstream cross-source title key before falling back to a
    # source-specific identifier. This preserves deterministic title-based
    # merging when DOI/arXiv IDs are unavailable.
    if existing_key.startswith("title:"):
        return existing_key

    openalex_id = str(external_ids.get("openalex_id") or "").strip().lower()
    if openalex_id:
        return f"openalex:{openalex_id}"
    if existing_key.startswith("openalex:"):
        return existing_key

    if existing_key:
        return existing_key

    normalized_title = str(candidate.get("normalized_title") or candidate.get("title") or "").strip().lower()
    if normalized_title:
        title_hash = hashlib.sha256(normalized_title.encode("utf-8")).hexdigest()[:16]
        return f"title:{title_hash}"

    return f"candidate_id:{candidate.get('candidate_id', 'unknown')}"


def _normalize_provenance(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Keep provenance usable even when upstream metadata is partial."""
    raw_provenance = candidate.get("provenance")
    source_name = str(candidate.get("source_name") or "unknown")
    matched_query = str(candidate.get("matched_query") or "")
    degraded = False
    normalized: list[dict[str, Any]] = []

    if isinstance(raw_provenance, list):
        for item in raw_provenance:
            if not isinstance(item, dict):
                degraded = True
                continue
            row = dict(item)
            if not row.get("source_name"):
                row["source_name"] = source_name
                degraded = True
            if "matched_query" not in row or row.get("matched_query") is None:
                row["matched_query"] = matched_query
                degraded = True
            normalized.append(row)
    elif raw_provenance is not None:
        degraded = True

    if not normalized:
        normalized.append(
            {
                "source_name": source_name,
                "matched_query": matched_query,
                "retrieval_mode": "dedup_adapter_fallback",
            }
        )
        degraded = True

    return normalized, degraded


def _select_best_version(
    current_best: dict[str, Any],
    new_candidate: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Select the best representative for two candidates with one dedupe key."""
    current_citations = current_best.get("citation_count") or 0
    new_citations = new_candidate.get("citation_count") or 0
    current_year = current_best.get("year") or 0
    new_year = new_candidate.get("year") or 0

    if new_citations > current_citations:
        return new_candidate, "higher_citations"
    if new_citations < current_citations:
        return current_best, "higher_citations"
    if new_year > current_year:
        return new_candidate, "more_recent"
    if new_year < current_year:
        return current_best, "more_recent"

    current_url = str(current_best.get("source_url") or "")
    new_url = str(new_candidate.get("source_url") or "")
    current_has_arxiv_pdf = "arxiv.org/pdf" in current_url.lower()
    new_has_arxiv_pdf = "arxiv.org/pdf" in new_url.lower()
    if new_has_arxiv_pdf and not current_has_arxiv_pdf:
        return new_candidate, "arxiv_pdf_preferred"
    if current_has_arxiv_pdf and not new_has_arxiv_pdf:
        return current_best, "arxiv_pdf_preferred"

    return current_best, "first_seen"


def _empty_report() -> dict[str, Any]:
    return {
        "input_count": 0,
        "output_count": 0,
        "duplicates_removed": 0,
        "multi_source_candidates": 0,
        "degraded_provenance_count": 0,
        "risk_flags": [],
        "dedup_keys_used": {
            "doi": 0,
            "arxiv_id": 0,
            "openalex_id": 0,
            "normalized_title": 0,
        },
    }


def deduplicate_candidates(input_data: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate metadata candidates and merge source provenance."""
    candidates = input_data.get("metadata_candidates", [])
    if not candidates:
        return {"deduped_candidates": [], "dedup_report": _empty_report()}

    best_by_key: dict[str, dict[str, Any]] = {}
    provenance_by_key: dict[str, list[dict[str, Any]]] = {}
    sources_by_key: dict[str, set[str]] = {}
    dedup_reason_by_key: dict[str, str] = {}
    degraded_candidate_ids: set[str] = set()

    for candidate in candidates:
        dedupe_key = _compute_dedupe_key(candidate)
        source_name = str(candidate.get("source_name") or "unknown")
        sources_by_key.setdefault(dedupe_key, set()).add(source_name)

        provenance, degraded = _normalize_provenance(candidate)
        provenance_by_key.setdefault(dedupe_key, []).extend(provenance)
        if degraded:
            degraded_candidate_ids.add(str(candidate.get("candidate_id") or dedupe_key))

        if dedupe_key not in best_by_key:
            best_by_key[dedupe_key] = candidate
            dedup_reason_by_key[dedupe_key] = "unique"
        else:
            best_candidate, reason = _select_best_version(best_by_key[dedupe_key], candidate)
            best_by_key[dedupe_key] = best_candidate
            dedup_reason_by_key[dedupe_key] = reason

    deduped_candidates: list[dict[str, Any]] = []
    dedup_keys_used = {"doi": 0, "arxiv_id": 0, "openalex_id": 0, "normalized_title": 0}

    for dedupe_key, candidate in best_by_key.items():
        if dedupe_key.startswith("doi:"):
            dedup_keys_used["doi"] += 1
        elif dedupe_key.startswith("arxiv:"):
            dedup_keys_used["arxiv_id"] += 1
        elif dedupe_key.startswith("openalex:"):
            dedup_keys_used["openalex_id"] += 1
        elif dedupe_key.startswith("title:") or dedupe_key.startswith("title_hash:"):
            dedup_keys_used["normalized_title"] += 1

        output_candidate = dict(candidate)
        output_candidate["dedupe_key"] = dedupe_key
        output_candidate["matched_source_count"] = len(sources_by_key[dedupe_key])
        output_candidate["dedup_reason"] = dedup_reason_by_key[dedupe_key]
        output_candidate["provenance"] = provenance_by_key[dedupe_key]
        deduped_candidates.append(output_candidate)

    deduped_candidates.sort(
        key=lambda c: (
            -(c.get("citation_count") or 0),
            -(c.get("year") or 0),
            str(c.get("title") or ""),
            str(c.get("dedupe_key") or ""),
        )
    )

    risk_flags: list[str] = []
    if degraded_candidate_ids:
        risk_flags.append("degraded_provenance_recovered")

    return {
        "deduped_candidates": deduped_candidates,
        "dedup_report": {
            "input_count": len(candidates),
            "output_count": len(deduped_candidates),
            "duplicates_removed": len(candidates) - len(deduped_candidates),
            "multi_source_candidates": sum(1 for sources in sources_by_key.values() if len(sources) >= 2),
            "degraded_provenance_count": len(degraded_candidate_ids),
            "risk_flags": risk_flags,
            "dedup_keys_used": dedup_keys_used,
        },
    }

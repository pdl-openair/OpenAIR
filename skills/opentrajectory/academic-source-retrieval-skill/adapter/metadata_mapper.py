"""Map self-contained source results to output.schema.json MetadataCandidate."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any


def _normalize_title(title: str | None) -> str | None:
    if not title:
        return None
    normalized = unicodedata.normalize("NFKD", title.lower())
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _dedupe_key(
    doi: str | None,
    arxiv_id: str | None,
    openalex_id: str | None,
    semantic_scholar_id: str | None,
    title: str | None,
) -> str | None:
    if doi:
        return f"doi:{doi.lower()}"
    if arxiv_id:
        return f"arxiv:{arxiv_id.lower().split('v')[0]}"
    if openalex_id:
        return f"openalex:{openalex_id.lower()}"
    if semantic_scholar_id:
        return f"s2:{semantic_scholar_id.lower()}"
    if title:
        digest = hashlib.sha256(title.lower().encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"title:{digest}"
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_external_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _pmcid_article_url(pmcid: str | None) -> str | None:
    cleaned = _clean_external_id(pmcid)
    if not cleaned:
        return None
    normalized = cleaned if cleaned.upper().startswith("PMC") else f"PMC{cleaned}"
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{normalized}/"


def _resolve_literature_external_ids(
    lit: Any,
    *,
    doi: str | None,
    arxiv_id: str | None,
    openalex_id: str | None,
    semantic_scholar_id: str | None,
) -> dict[str, Any]:
    raw_metadata = getattr(lit, "raw_metadata", None)
    external_ids = raw_metadata.get("externalIds") if isinstance(raw_metadata, dict) and isinstance(raw_metadata.get("externalIds"), dict) else {}
    corpus_id = _clean_external_id(external_ids.get("CorpusId"))
    pmid = _clean_external_id(external_ids.get("PubMed"))
    pmcid = _clean_external_id(external_ids.get("PubMedCentral"))
    return {
        "doi": doi,
        "arxiv_id": arxiv_id,
        "openalex_id": openalex_id,
        "semantic_scholar_id": semantic_scholar_id,
        "corpus_id": corpus_id,
        "pmid": pmid,
        "pmcid": pmcid,
    }


def _supplement_access_urls(
    *,
    source_url: str | None,
    landing_page_url: str | None,
    doi: str | None,
    pmcid: str | None,
) -> tuple[str | None, str | None]:
    source = _clean_external_id(source_url)
    landing = _clean_external_id(landing_page_url)
    pmc_url = _pmcid_article_url(pmcid)
    if not source and pmc_url:
        source = pmc_url
    if pmc_url and (not landing or "semanticscholar.org" in landing.lower()):
        landing = pmc_url
    if not landing and doi:
        landing = f"https://doi.org/{doi}"
    return source, landing


def _provenance(
    *,
    source_name: str,
    original_query: str,
    query_index: int,
    result_index: int,
    search_intent: str | None,
    variant_type: str | None,
    retrieval_mode: str,
) -> list[dict[str, Any]]:
    return [{
        "source_name": source_name,
        "matched_query": original_query,
        "search_intent": search_intent,
        "query_variant_type": variant_type,
        "source_constraints": [],
        "query_index": query_index,
        "source_result_index": result_index,
        "source_rank": result_index + 1,
        "retrieved_at": _now_iso(),
        "retrieval_mode": retrieval_mode,
    }]


def arxiv_hit_to_candidate(
    hit: Any,
    original_query: str,
    query_index: int,
    result_index: int,
    search_intent: str | None,
    variant_type: str | None,
    *,
    retrieval_mode: str = "expanded_query_variant",
    timeout_s: int = 30,
    decision_evidence_slot: str | None = None,
    slot_reason: str | None = None,
    targeted_from_missing_item: bool | None = None,
    retrieval_intent: str | None = None,
    slot_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Convert an arXiv Atom hit without issuing a second metadata request."""
    del timeout_s
    arxiv_id = hit.arxiv_id.split("v")[0]
    doi = getattr(hit, "doi", None)

    candidate = {
        "candidate_id": f"arxiv_{query_index}_{result_index}",
        "dedupe_key": _dedupe_key(doi, arxiv_id, None, None, hit.title),
        "normalized_title": _normalize_title(hit.title),
        "external_ids": {
            "doi": doi,
            "arxiv_id": arxiv_id,
            "openalex_id": None,
            "semantic_scholar_id": None,
            "corpus_id": None,
            "pmid": None,
            "pmcid": None,
        },
        "title": hit.title,
        "abstract": getattr(hit, "abstract", None),
        "authors": getattr(hit, "authors", None),
        "year": getattr(hit, "year", None),
        "venue": getattr(hit, "venue", None),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "citation_count": None,
        "source_url": hit.pdf_url,
        "landing_page_url": hit.abs_url,
        "source_name": "arxiv",
        "matched_query": original_query,
        "search_intent": search_intent,
        "query_variant_type": variant_type,
        "provenance": _provenance(
            source_name="arxiv",
            original_query=original_query,
            query_index=query_index,
            result_index=result_index,
            search_intent=search_intent,
            variant_type=variant_type,
            retrieval_mode=retrieval_mode,
        ),
        "raw_metadata": {
            "arxiv_id": hit.arxiv_id,
            "abs_url": hit.abs_url,
            "comment": getattr(hit, "comment", None),
        },
        "retrieval_warnings": [],
    }

    if decision_evidence_slot is not None:
        candidate["decision_evidence_slot"] = decision_evidence_slot
    if slot_reason is not None:
        candidate["slot_reason"] = slot_reason
    if targeted_from_missing_item is not None:
        candidate["targeted_from_missing_item"] = targeted_from_missing_item
    if retrieval_intent is not None:
        candidate["retrieval_intent"] = retrieval_intent
    if slot_ids is not None:
        candidate["slot_ids"] = slot_ids

    return candidate


def lit_candidate_to_candidate(
    lit: Any,
    source_name: str,
    original_query: str,
    query_index: int,
    result_index: int,
    search_intent: str | None,
    variant_type: str | None,
    *,
    retrieval_mode: str = "expanded_query_variant",
    openalex_id: str | None = None,
    semantic_scholar_id: str | None = None,
    decision_evidence_slot: str | None = None,
    slot_reason: str | None = None,
    targeted_from_missing_item: bool | None = None,
    retrieval_intent: str | None = None,
    slot_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Convert a standalone literature result to MetadataCandidate."""
    arxiv_id = lit.arxiv_id.split("v")[0] if getattr(lit, "arxiv_id", None) else None
    doi = getattr(lit, "doi", None)
    external_ids = _resolve_literature_external_ids(
        lit,
        doi=doi,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        semantic_scholar_id=semantic_scholar_id,
    )
    source_url, landing_page_url = _supplement_access_urls(
        source_url=getattr(lit, "pdf_url", None),
        landing_page_url=getattr(lit, "landing_page_url", None),
        doi=doi,
        pmcid=external_ids.get("pmcid"),
    )

    candidate = {
        "candidate_id": f"{source_name}_{query_index}_{result_index}",
        "dedupe_key": _dedupe_key(doi, arxiv_id, openalex_id, semantic_scholar_id, lit.title),
        "normalized_title": _normalize_title(lit.title),
        "external_ids": external_ids,
        "title": lit.title,
        "abstract": getattr(lit, "abstract_snippet", None),
        "authors": getattr(lit, "authors", None),
        "year": getattr(lit, "year", None),
        "venue": getattr(lit, "venue", None),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "citation_count": getattr(lit, "citation_count", None),
        "source_url": source_url,
        "landing_page_url": landing_page_url,
        "source_name": source_name,
        "matched_query": original_query,
        "search_intent": search_intent,
        "query_variant_type": variant_type,
        "provenance": _provenance(
            source_name=source_name,
            original_query=original_query,
            query_index=query_index,
            result_index=result_index,
            search_intent=search_intent,
            variant_type=variant_type,
            retrieval_mode=retrieval_mode,
        ),
        "raw_metadata": getattr(lit, "raw_metadata", None) or {
            "dedupe_key": lit.dedupe_key,
            "source_backend": lit.source_backend,
        },
        "retrieval_warnings": [],
    }

    if decision_evidence_slot is not None:
        candidate["decision_evidence_slot"] = decision_evidence_slot
    if slot_reason is not None:
        candidate["slot_reason"] = slot_reason
    if targeted_from_missing_item is not None:
        candidate["targeted_from_missing_item"] = targeted_from_missing_item
    if retrieval_intent is not None:
        candidate["retrieval_intent"] = retrieval_intent
    if slot_ids is not None:
        candidate["slot_ids"] = slot_ids

    return candidate

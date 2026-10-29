"""
query_adapter.py — Convert generic Boolean query variants to source-specific queries.

P0 rules for arXiv:
- Extract quoted phrases first
- strict: keep first 2-3 core phrases as phrase search
- broad: split into 1-2 short keyword groups
- Single arXiv query: 3-6 keywords/phrases
- Skip and warn if query is too long or clearly ill-formed
- Never auto-translate CJK
"""
from __future__ import annotations

import re
from typing import NamedTuple


class AdaptedQuery(NamedTuple):
    query: str
    source: str
    original: str
    warning: str | None  # None = ok, str = skip reason


def _extract_quoted_phrases(query: str) -> list[str]:
    return re.findall(r'"([^"]+)"', query)


def _strip_boolean_to_plain(query: str) -> str:
    """Remove Boolean operators and parentheses, collapse whitespace."""
    q = re.sub(r'\b(AND|OR|NOT)\b', ' ', query)
    q = re.sub(r'[()"]', ' ', q)
    return re.sub(r'\s+', ' ', q).strip()


def _has_cjk(text: str) -> bool:
    return bool(re.search(r'[一-鿿㐀-䶿豈-﫿'
                          r'぀-ヿ가-힯]', text))


def adapt_for_arxiv(query: str, variant_type: str | None) -> AdaptedQuery:
    """
    Convert a Boolean query to a plain keyword string for search_arxiv().

    search_arxiv() handles URL construction internally; we must pass plain
    keywords only — no all: prefixes, no Boolean operators, no quotes.

    P0 rules:
    - strict: extract quoted phrases, keep first 2-3, join as plain text
    - broad/fallback: extract first 1-2 phrases or first 3 plain words
    - Result: 3-6 meaningful tokens, ≤120 chars
    """
    if _has_cjk(query):
        return AdaptedQuery("", "arxiv", query, "skipped: CJK characters in query")

    phrases = _extract_quoted_phrases(query)

    if variant_type == "strict" or variant_type is None:
        if phrases:
            # Join first 2-3 phrases as plain text (search_arxiv will phrase-search them)
            arxiv_q = " ".join(phrases[:3])
        else:
            plain = _strip_boolean_to_plain(query)
            words = plain.split()
            if not words:
                return AdaptedQuery("", "arxiv", query, "skipped: empty query after stripping")
            arxiv_q = " ".join(words[:5])
    else:
        # broad / fallback: shorter, 1-2 phrases or 3 words
        if phrases:
            arxiv_q = " ".join(phrases[:2])
        else:
            plain = _strip_boolean_to_plain(query)
            words = plain.split()
            if not words:
                return AdaptedQuery("", "arxiv", query, "skipped: empty query after stripping")
            arxiv_q = " ".join(words[:3])

    # Truncate to 120 chars (search_arxiv has its own 400-char limit, but shorter is safer)
    if len(arxiv_q) > 120:
        arxiv_q = arxiv_q[:120].rsplit(" ", 1)[0]
        if not arxiv_q:
            return AdaptedQuery("", "arxiv", query, "skipped: empty after truncation")

    tokens = re.findall(r'\w+', arxiv_q)
    if len(tokens) < 2:
        return AdaptedQuery("", "arxiv", query,
                            f"skipped: too few tokens ({len(tokens)}) after adaptation")

    return AdaptedQuery(arxiv_q, "arxiv", query, None)


def adapt_for_openalex(query: str) -> AdaptedQuery:
    """OpenAlex search accepts plain text; strip Boolean syntax."""
    if _has_cjk(query):
        return AdaptedQuery("", "openalex", query, "skipped: CJK characters in query")
    plain = _strip_boolean_to_plain(query)
    if not plain:
        return AdaptedQuery("", "openalex", query, "skipped: empty query after stripping")
    return AdaptedQuery(plain[:400], "openalex", query, None)


def adapt_for_semantic_scholar(query: str) -> AdaptedQuery:
    """Semantic Scholar search accepts plain text; strip Boolean syntax."""
    if _has_cjk(query):
        return AdaptedQuery("", "semantic_scholar", query, "skipped: CJK characters in query")
    plain = _strip_boolean_to_plain(query)
    if not plain:
        return AdaptedQuery("", "semantic_scholar", query, "skipped: empty query after stripping")
    return AdaptedQuery(plain[:400], "semantic_scholar", query, None)


def adapt_for_deepxiv(query: str) -> AdaptedQuery:
    """DeepXiv accepts plain text; strip Boolean syntax. Max 500 chars."""
    if _has_cjk(query):
        return AdaptedQuery("", "deepxiv", query, "skipped: CJK characters in query")
    plain = _strip_boolean_to_plain(query)
    if not plain:
        return AdaptedQuery("", "deepxiv", query, "skipped: empty query after stripping")
    return AdaptedQuery(plain[:500], "deepxiv", query, None)


def adapt_query_for_source(query: str, source: str,
                           variant_type: str | None = None) -> AdaptedQuery:
    """Dispatch to source-specific adapter."""
    if source == "arxiv":
        return adapt_for_arxiv(query, variant_type)
    if source == "openalex":
        return adapt_for_openalex(query)
    if source == "semantic_scholar":
        return adapt_for_semantic_scholar(query)
    if source == "deepxiv":
        return adapt_for_deepxiv(query)
    return AdaptedQuery("", source, query, f"skipped: unknown source '{source}'")

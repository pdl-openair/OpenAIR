"""Self-contained academic metadata clients for the Phase4 retrieval Skill.

The first deployable slice intentionally keeps the runtime small:
- OpenAlex metadata search
- arXiv Atom metadata search
- Semantic Scholar metadata search as an optional, non-blocking source

No PDF bytes are downloaded.  The clients use only the Python standard library so
OpenAIR and the Skill Lab deployment snapshot do not depend on the legacy
OpenTrajectory ``04_backend`` directory.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

DEFAULT_USER_AGENT = os.environ.get(
    "OPENTRAJECTORY_RETRIEVAL_USER_AGENT",
    "OpenAIR-Skill-Lab/0.7a (academic metadata retrieval)",
)
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
ARXIV_NS = "http://arxiv.org/schemas/atom"

_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT: dict[str, float] = {}


@dataclass(frozen=True)
class ArxivHit:
    arxiv_id: str
    title: str
    abs_url: str | None
    pdf_url: str | None
    abstract: str | None = None
    comment: str | None = None
    authors: list[str] | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None


@dataclass(frozen=True)
class LiteratureCandidate:
    title: str
    dedupe_key: str
    source_backend: str
    abstract_snippet: str | None = None
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    pdf_url: str | None = None
    landing_page_url: str | None = None
    authors: list[str] | None = None
    venue: str | None = None
    raw_metadata: dict[str, Any] | None = None


def _positive_float_env(name: str, fallback: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        return fallback
    return value if value >= 0 else fallback


def _wait_for(source: str, minimum_interval_seconds: float) -> None:
    if os.environ.get("OPENTRAJECTORY_RETRIEVAL_DISABLE_RATE_LIMIT") == "1":
        return
    interval = _positive_float_env(
        f"OPENTRAJECTORY_{source.upper()}_MIN_INTERVAL_SECONDS",
        minimum_interval_seconds,
    )
    if interval <= 0:
        return
    with _RATE_LOCK:
        last = _LAST_REQUEST_AT.get(source)
        now = time.monotonic()
        if last is not None:
            remaining = interval - (now - last)
            if remaining > 0:
                time.sleep(remaining)
        _LAST_REQUEST_AT[source] = time.monotonic()


def _get_bytes(
    url: str,
    *,
    timeout_s: int,
    source: str,
    min_interval_seconds: float,
    headers: dict[str, str] | None = None,
) -> bytes:
    _wait_for(source, min_interval_seconds)
    request_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json, application/atom+xml;q=0.9, */*;q=0.1"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.read()


def _json_get(
    url: str,
    *,
    timeout_s: int,
    source: str,
    min_interval_seconds: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw = _get_bytes(
        url,
        timeout_s=timeout_s,
        source=source,
        min_interval_seconds=min_interval_seconds,
        headers=headers,
    )
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{source} response must be a JSON object")
    return payload


def _clean_doi(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.IGNORECASE)
    return value or None


def _clean_arxiv_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    value = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    return value or None


def _reconstruct_openalex_abstract(index: Any) -> str | None:
    if not isinstance(index, dict):
        return None
    positioned: list[tuple[int, str]] = []
    for token, positions in index.items():
        if not isinstance(token, str) or not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                positioned.append((position, token))
    if not positioned:
        return None
    positioned.sort(key=lambda item: item[0])
    return " ".join(token for _, token in positioned)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def search_openalex(
    query: str,
    *,
    max_results: int = 10,
    timeout_s: int = 45,
    mailto: str | None = None,
    api_key: str | None = None,
) -> list[LiteratureCandidate]:
    """Search OpenAlex Works and return normalized metadata candidates."""
    base_url = (os.environ.get("OPENALEX_API_BASE_URL") or "https://api.openalex.org/works").rstrip("?")
    resolved_api_key = (api_key or os.environ.get("OPENALEX_API_KEY") or "").strip() or None
    resolved_mailto = (mailto or os.environ.get("OPENALEX_MAILTO") or "").strip() or None
    params: dict[str, str] = {
        "search": query,
        "per_page": str(max(1, min(int(max_results), 100))),
    }
    if resolved_api_key:
        params["api_key"] = resolved_api_key
    if resolved_mailto:
        params["mailto"] = resolved_mailto
    separator = "&" if "?" in base_url else "?"
    payload = _json_get(
        f"{base_url}{separator}{urllib.parse.urlencode(params)}",
        timeout_s=timeout_s,
        source="openalex",
        min_interval_seconds=0.1,
    )
    results = payload.get("results") or []
    if not isinstance(results, list):
        raise RuntimeError("openalex results must be a list")

    candidates: list[LiteratureCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        openalex_url = item.get("id") if isinstance(item.get("id"), str) else None
        openalex_id = openalex_url.rsplit("/", 1)[-1] if openalex_url else None
        title = str(_first_non_empty(item.get("display_name"), item.get("title"), "")).strip()
        if not title:
            continue
        ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
        arxiv_id = _clean_arxiv_id(ids.get("arxiv"))
        doi = _clean_doi(_first_non_empty(item.get("doi"), ids.get("doi")))
        best_oa = item.get("best_oa_location") if isinstance(item.get("best_oa_location"), dict) else {}
        primary = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
        authorships = item.get("authorships") if isinstance(item.get("authorships"), list) else []
        authors: list[str] = []
        for authorship in authorships:
            author = authorship.get("author") if isinstance(authorship, dict) and isinstance(authorship.get("author"), dict) else {}
            display_name = author.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                authors.append(display_name.strip())
        source = primary.get("source") if isinstance(primary.get("source"), dict) else {}
        venue = _first_non_empty(source.get("display_name"), item.get("host_venue", {}).get("display_name") if isinstance(item.get("host_venue"), dict) else None)
        pdf_url = _first_non_empty(best_oa.get("pdf_url"), primary.get("pdf_url"))
        landing_page_url = _first_non_empty(primary.get("landing_page_url"), openalex_url, f"https://doi.org/{doi}" if doi else None)
        dedupe_key = f"openalex:{openalex_id}" if openalex_id else f"title:{title.lower()}"
        candidates.append(LiteratureCandidate(
            title=title,
            dedupe_key=dedupe_key,
            source_backend="openalex",
            abstract_snippet=_reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
            year=item.get("publication_year") if isinstance(item.get("publication_year"), int) else None,
            doi=doi,
            arxiv_id=arxiv_id,
            pdf_url=pdf_url if isinstance(pdf_url, str) else None,
            landing_page_url=landing_page_url if isinstance(landing_page_url, str) else None,
            authors=authors or None,
            venue=venue if isinstance(venue, str) else None,
            raw_metadata={"id": openalex_url, "type": item.get("type"), "cited_by_count": item.get("cited_by_count")},
        ))
    return candidates


def search_arxiv(query: str, *, max_results: int = 10, timeout_s: int = 45) -> list[ArxivHit]:
    """Search the arXiv API and parse Atom metadata without downloading PDFs."""
    base_url = (os.environ.get("ARXIV_API_BASE_URL") or "https://export.arxiv.org/api/query").rstrip("?")
    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(max(1, min(int(max_results), 100))),
    }
    separator = "&" if "?" in base_url else "?"
    raw = _get_bytes(
        f"{base_url}{separator}{urllib.parse.urlencode(params)}",
        timeout_s=timeout_s,
        source="arxiv",
        min_interval_seconds=3.0,
        headers={"Accept": "application/atom+xml"},
    )
    return _parse_arxiv_atom(raw)


def fetch_arxiv_by_id(arxiv_id: str, *, timeout_s: int = 45) -> ArxivHit | None:
    """Fetch one arXiv Atom entry by id without downloading PDFs."""
    cleaned = _clean_arxiv_id(arxiv_id)
    if not cleaned:
        return None
    base_url = (os.environ.get("ARXIV_API_BASE_URL") or "https://export.arxiv.org/api/query").rstrip("?")
    params = {"id_list": cleaned}
    separator = "&" if "?" in base_url else "?"
    raw = _get_bytes(
        f"{base_url}{separator}{urllib.parse.urlencode(params)}",
        timeout_s=timeout_s,
        source="arxiv",
        min_interval_seconds=3.0,
        headers={"Accept": "application/atom+xml"},
    )
    hits = _parse_arxiv_atom(raw)
    return hits[0] if hits else None


def _parse_arxiv_atom(raw: bytes) -> list[ArxivHit]:
    root = ET.fromstring(raw)
    hits: list[ArxivHit] = []
    for entry in root.findall("a:entry", ATOM_NS):
        entry_id = (entry.findtext("a:id", default="", namespaces=ATOM_NS) or "").strip()
        arxiv_id = _clean_arxiv_id(entry_id)
        title = re.sub(r"\s+", " ", (entry.findtext("a:title", default="", namespaces=ATOM_NS) or "").strip())
        if not arxiv_id or not title:
            continue
        abstract = re.sub(r"\s+", " ", (entry.findtext("a:summary", default="", namespaces=ATOM_NS) or "").strip()) or None
        comment = re.sub(r"\s+", " ", (entry.findtext(f"{{{ARXIV_NS}}}comment", default="") or "").strip()) or None
        authors = [
            (name.text or "").strip()
            for author in entry.findall("a:author", ATOM_NS)
            if (name := author.find("a:name", ATOM_NS)) is not None and (name.text or "").strip()
        ]
        published = (entry.findtext("a:published", default="", namespaces=ATOM_NS) or "").strip()
        year_match = re.match(r"(\d{4})", published)
        primary_category = entry.find(f"{{{ARXIV_NS}}}primary_category")
        doi = _clean_doi(entry.findtext(f"{{{ARXIV_NS}}}doi", default=""))
        abs_url: str | None = entry_id or None
        pdf_url: str | None = None
        for link in entry.findall("a:link", ATOM_NS):
            href = link.get("href")
            if not href:
                continue
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = href
            elif link.get("rel") == "alternate":
                abs_url = href
        hits.append(ArxivHit(
            arxiv_id=arxiv_id,
            title=title,
            abs_url=abs_url,
            pdf_url=pdf_url,
            abstract=abstract,
            comment=comment,
            authors=authors or None,
            year=int(year_match.group(1)) if year_match else None,
            venue=f"arXiv:{primary_category.get('term', '')}" if primary_category is not None else "arXiv",
            doi=doi,
        ))
    return hits


def search_semantic_scholar(
    query: str,
    *,
    api_key: str | None = None,
    max_results: int = 10,
    timeout_s: int = 45,
    require_pdf: bool = False,
) -> list[LiteratureCandidate]:
    """Search Semantic Scholar metadata. Failures are handled by the orchestrator."""
    base_url = (os.environ.get("SEMANTIC_SCHOLAR_API_BASE_URL") or "https://api.semanticscholar.org/graph/v1/paper/search").rstrip("?")
    params = {
        "query": query,
        "limit": str(max(1, min(int(max_results), 100))),
        "fields": "paperId,title,abstract,year,venue,externalIds,openAccessPdf,url,authors",
    }
    separator = "&" if "?" in base_url else "?"
    resolved_api_key = (api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY") or "").strip() or None
    headers = {"x-api-key": resolved_api_key} if resolved_api_key else None
    payload = _json_get(
        f"{base_url}{separator}{urllib.parse.urlencode(params)}",
        timeout_s=timeout_s,
        source="semantic_scholar",
        min_interval_seconds=0.2,
        headers=headers,
    )
    results = payload.get("data") or []
    if not isinstance(results, list):
        raise RuntimeError("semantic_scholar data must be a list")

    candidates: list[LiteratureCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        paper_id = item.get("paperId")
        title = item.get("title")
        if not isinstance(paper_id, str) or not isinstance(title, str) or not title.strip():
            continue
        external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
        open_access_pdf = item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else {}
        pdf_url = open_access_pdf.get("url") if isinstance(open_access_pdf.get("url"), str) else None
        if require_pdf and not pdf_url:
            continue
        authors_raw = item.get("authors") if isinstance(item.get("authors"), list) else []
        authors = [author.get("name").strip() for author in authors_raw if isinstance(author, dict) and isinstance(author.get("name"), str) and author.get("name").strip()]
        doi = _clean_doi(external_ids.get("DOI"))
        arxiv_id = _clean_arxiv_id(external_ids.get("ArXiv"))
        candidates.append(LiteratureCandidate(
            title=title.strip(),
            dedupe_key=f"s2:{paper_id}",
            source_backend="semantic_scholar",
            abstract_snippet=item.get("abstract") if isinstance(item.get("abstract"), str) else None,
            year=item.get("year") if isinstance(item.get("year"), int) else None,
            doi=doi,
            arxiv_id=arxiv_id,
            pdf_url=pdf_url,
            landing_page_url=item.get("url") if isinstance(item.get("url"), str) else None,
            authors=authors or None,
            venue=item.get("venue") if isinstance(item.get("venue"), str) else None,
            raw_metadata={"paperId": paper_id, "externalIds": external_ids},
        ))
    return candidates

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

try:
    from app.config import settings  # noqa: F401 - importing config loads repo .env safely.
except Exception:
    pass


SOURCE_MODE_LABELS = {
    "metadata_only": "仅题录",
    "abstract_available": "有摘要",
    "arxiv_fulltext": "arXiv 全文线索",
    "oa_located": "开放获取线索",
    "user_provided": "用户提供",
    "blocked": "受阻",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def configured_key_status() -> dict[str, str]:
    def present(name: str) -> bool:
        value = (os.environ.get(name) or "").strip()
        return bool(value and value.lower() not in {"todo", "change_me", "xxx", "your_key"})

    return {
        "OPENALEX_MAILTO": "present" if present("OPENALEX_MAILTO") else "missing",
        "SEMANTIC_SCHOLAR_API_KEY": "present"
        if present("SEMANTIC_SCHOLAR_API_KEY") or present("S2_API_KEY")
        else "missing",
        "UNPAYWALL_EMAIL": "present" if present("UNPAYWALL_EMAIL") else "missing",
        "ARXIV_API_KEY": "not_configured_optional",
    }


@dataclass
class Attempt:
    source_api: str
    query: str
    status: str
    retrieved_at: str
    error: str | None = None
    result_count: int = 0


@dataclass
class SourceRow:
    source_ref: str
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    url: str | None
    abstract: str | None
    source_api: str
    external_id: str
    retrieved_at: str
    source_mode: str
    readiness_label: str
    selection_reason: str
    provenance: dict[str, Any]
    score: float = 0.0
    relevance_score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    matched_phrases: list[str] = field(default_factory=list)
    missing_core_terms: list[str] = field(default_factory=list)
    relevance_reason: str = ""
    is_relevant_enough: bool = False
    selection_status: str = "candidate"
    exclusion_reason: str | None = None


@dataclass
class LiteratureRun:
    topic: str
    strategy: dict[str, Any]
    key_status: dict[str, str]
    attempted_apis: list[Attempt] = field(default_factory=list)
    sources: list[SourceRow] = field(default_factory=list)
    top_k: list[dict[str, Any]] = field(default_factory=list)
    failure_state: dict[str, Any] | None = None
    relevance_gate: dict[str, Any] = field(default_factory=dict)

    @property
    def source_count(self) -> int:
        return sum(a.result_count for a in self.attempted_apis)

    @property
    def deduped_count(self) -> int:
        return len(self.sources)


def build_search_strategy(topic: str, *, top_k: int = 8) -> dict[str, Any]:
    """Describe deterministic retrieval execution only."""
    normalized = " ".join((topic or "").strip().split())
    return {
        "originalTopic": topic,
        "normalizedQuery": normalized,
        "queryVariants": [normalized] if normalized else [],
        "optionalKeywords": [],
        "coreTerms": [],
        "queryPhrases": [],
        "queryLooksRandom": False,
        "sourcePriority": [
            "OpenAlex",
            "Semantic Scholar optional",
            "arXiv",
            "Unpaywall optional",
        ],
        "topK": top_k,
        "semanticScreening": "pending_agent_judgment",
    }



STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "using",
    "with",
}

TASK_TERMS = {
    "benchmark",
    "benchmarks",
    "dataset",
    "datasets",
    "evaluation",
    "evaluations",
    "survey",
    "review",
    "comparison",
    "comparative",
}


def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9-]*", (text or "").lower()) if len(w) > 1]


def _term_variants(term: str) -> set[str]:
    variants = {term}
    if term.endswith("ies") and len(term) > 4:
        variants.add(term[:-3] + "y")
    if term.endswith("s") and len(term) > 3:
        variants.add(term[:-1])
    if term.endswith("network"):
        variants.add(term + "s")
    return variants


def _contains_term(text: str, term: str) -> bool:
    return any(re.search(rf"\b{re.escape(v)}\b", text) for v in _term_variants(term))


def _core_terms(topic: str) -> list[str]:
    terms = [t for t in _tokenize(topic) if t not in STOPWORDS and len(t) > 2]
    return list(dict.fromkeys(terms))[:12]


def _query_phrases(topic: str) -> list[str]:
    tokens = _core_terms(topic)
    phrases: list[str] = []
    normalized = " ".join((topic or "").lower().split())
    known = [
        "retrieval augmented generation",
        "molecular property prediction",
        "graph neural network",
        "graph neural networks",
    ]
    phrases.extend(p for p in known if p in normalized)
    for size in (4, 3, 2):
        for idx in range(0, max(0, len(tokens) - size + 1)):
            phrase = " ".join(tokens[idx : idx + size])
            if any(term in TASK_TERMS for term in phrase.split()) or size >= 3:
                phrases.append(phrase)
    return list(dict.fromkeys(phrases))[:10]


def _query_looks_random(topic: str) -> bool:
    tokens = _tokenize(topic)
    if not tokens:
        return False
    suspicious = 0
    current_year = datetime.now(timezone.utc).year
    for token in tokens:
        if token in {"nonexisting", "nonexistent", "nosuchpaper", "imaginary", "unicorn"}:
            suspicious += 2
        if re.search(r"(zz|zxq|qxz|jvpl|abcde)", token):
            suspicious += 2
        if len(token) >= 8 and len(re.findall(r"[aeiou]", token)) <= 1:
            suspicious += 2
        if token.isdigit() and int(token) > current_year + 5:
            suspicious += 1
    return suspicious >= 3


def _http_json(url: str, *, timeout: float = 12.0, headers: dict[str, str] | None = None) -> Any:
    req = Request(url, headers={"User-Agent": "OpenAIR-C2B/0.1", **(headers or {})})
    with urlopen(req, timeout=timeout) as res:  # noqa: S310 - controlled scholarly APIs only.
        return json.loads(res.read().decode("utf-8"))


def _http_text(url: str, *, timeout: float = 12.0) -> str:
    req = Request(url, headers={"User-Agent": "OpenAIR-C2B/0.1"})
    with urlopen(req, timeout=timeout) as res:  # noqa: S310 - controlled scholarly APIs only.
        return res.read().decode("utf-8", errors="replace")


def _year_from_openalex(work: dict[str, Any]) -> int | None:
    year = work.get("publication_year")
    return year if isinstance(year, int) else None


def _safe_http_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTPError:{exc.code}"
    return exc.__class__.__name__


def _openalex_sources(query: str, *, limit: int, timeout: float) -> tuple[list[SourceRow], Attempt]:
    retrieved_at = utc_now()
    params = {
        "search": query,
        "per-page": str(limit),
        "select": "id,doi,title,display_name,authorships,publication_year,primary_location,abstract_inverted_index",
    }
    mailto = (os.environ.get("OPENALEX_MAILTO") or "").strip()
    if mailto:
        params["mailto"] = mailto
    url = f"https://api.openalex.org/works?{urlencode(params)}"
    try:
        payload = _http_json(url, timeout=timeout)
        rows: list[SourceRow] = []
        for idx, work in enumerate(payload.get("results") or []):
            title = (work.get("title") or work.get("display_name") or "").strip()
            if not title:
                continue
            authors = []
            for authorship in work.get("authorships") or []:
                author = authorship.get("author") or {}
                name = (author.get("display_name") or "").strip()
                if name:
                    authors.append(name)
            abstract = _openalex_abstract(work.get("abstract_inverted_index"))
            primary = work.get("primary_location") or {}
            source = primary.get("source") or {}
            landing = primary.get("landing_page_url") or primary.get("pdf_url")
            is_oa = bool(primary.get("is_oa"))
            source_mode = "abstract_available" if abstract else "metadata_only"
            if is_oa:
                source_mode = "oa_located"
            external_id = work.get("id") or f"openalex:{idx}"
            rows.append(
                SourceRow(
                    source_ref=f"S{len(rows)+1}",
                    title=title,
                    authors=authors[:8],
                    year=_year_from_openalex(work),
                    venue=(source.get("display_name") or None),
                    doi=(work.get("doi") or "").replace("https://doi.org/", "") or None,
                    arxiv_id=None,
                    url=landing or work.get("id"),
                    abstract=abstract,
                    source_api="OpenAlex",
                    external_id=external_id,
                    retrieved_at=retrieved_at,
                    source_mode=source_mode,
                    readiness_label=SOURCE_MODE_LABELS[source_mode],
                    selection_reason="题名/摘要与检索式匹配；来自 OpenAlex 元数据。",
                    provenance={
                        "sourceApi": "OpenAlex",
                        "query": query,
                        "externalId": external_id,
                        "url": landing or work.get("id"),
                        "retrievedAt": retrieved_at,
                        "sourceMode": source_mode,
                    },
                )
            )
        return rows, Attempt("OpenAlex", query, "ok", retrieved_at, result_count=len(rows))
    except TimeoutError as exc:
        return [], Attempt("OpenAlex", query, "timeout", retrieved_at, error=str(exc))
    except Exception as exc:  # network/status/json all become bounded failure details
        return [], Attempt("OpenAlex", query, "failed", retrieved_at, error=_safe_http_error(exc))


def _openalex_abstract(inverted: Any) -> str | None:
    if not isinstance(inverted, dict) or not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted.items():
        if isinstance(indexes, list):
            for idx in indexes:
                if isinstance(idx, int):
                    positions.append((idx, word))
    if not positions:
        return None
    return " ".join(word for _, word in sorted(positions))[:1800]


def _arxiv_sources(query: str, *, limit: int, timeout: float) -> tuple[list[SourceRow], Attempt]:
    retrieved_at = utc_now()
    url = (
        "https://export.arxiv.org/api/query?"
        + urlencode({"search_query": f"all:{query}", "start": "0", "max_results": str(limit)})
    )
    try:
        xml = _http_text(url, timeout=timeout)
        root = ElementTree.fromstring(xml)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        rows: list[SourceRow] = []
        for entry in root.findall("atom:entry", ns):
            title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
            if not title:
                continue
            summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns) or "").split())
            entry_id = entry.findtext("atom:id", default="", namespaces=ns) or ""
            arxiv_id = entry_id.rstrip("/").split("/")[-1] if entry_id else None
            authors = [
                (author.findtext("atom:name", default="", namespaces=ns) or "").strip()
                for author in entry.findall("atom:author", ns)
            ]
            authors = [a for a in authors if a]
            published = entry.findtext("atom:published", default="", namespaces=ns) or ""
            year = int(published[:4]) if published[:4].isdigit() else None
            rows.append(
                SourceRow(
                    source_ref=f"S{len(rows)+1}",
                    title=title,
                    authors=authors[:8],
                    year=year,
                    venue="arXiv",
                    doi=None,
                    arxiv_id=arxiv_id,
                    url=entry_id,
                    abstract=summary or None,
                    source_api="arXiv",
                    external_id=arxiv_id or entry_id,
                    retrieved_at=retrieved_at,
                    source_mode="arxiv_fulltext",
                    readiness_label=SOURCE_MODE_LABELS["arxiv_fulltext"],
                    selection_reason="题名/摘要与检索式匹配；arXiv 提供摘要和全文线索。",
                    provenance={
                        "sourceApi": "arXiv",
                        "query": query,
                        "externalId": arxiv_id or entry_id,
                        "url": entry_id,
                        "retrievedAt": retrieved_at,
                        "sourceMode": "arxiv_fulltext",
                    },
                )
            )
        return rows, Attempt("arXiv", query, "ok", retrieved_at, result_count=len(rows))
    except TimeoutError as exc:
        return [], Attempt("arXiv", query, "timeout", retrieved_at, error=str(exc))
    except Exception as exc:
        return [], Attempt("arXiv", query, "failed", retrieved_at, error=_safe_http_error(exc))


def retrieve_arxiv_identifiers(identifiers: list[str], *, timeout: float = 15.0) -> tuple[list[SourceRow], Attempt]:
    """Resolve candidate arXiv identifiers against the public arXiv metadata API."""
    cleaned = list(dict.fromkeys(
        identifier.strip().lower().split("v")[0]
        for identifier in identifiers
        if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", identifier.strip(), re.IGNORECASE)
    ))
    retrieved_at = utc_now()
    query = ",".join(cleaned)
    if not cleaned:
        return [], Attempt("arXiv", query, "skipped", retrieved_at, error="no valid arXiv identifiers")
    url = "https://export.arxiv.org/api/query?" + urlencode({"id_list": query})
    try:
        xml = _http_text(url, timeout=timeout)
        root = ElementTree.fromstring(xml)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        rows: list[SourceRow] = []
        for entry in root.findall("atom:entry", ns):
            title = " ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split())
            entry_id = entry.findtext("atom:id", default="", namespaces=ns) or ""
            arxiv_id = entry_id.rstrip("/").split("/")[-1].split("v")[0] if entry_id else ""
            if not title or arxiv_id not in cleaned:
                continue
            authors = [
                (author.findtext("atom:name", default="", namespaces=ns) or "").strip()
                for author in entry.findall("atom:author", ns)
            ]
            published = entry.findtext("atom:published", default="", namespaces=ns) or ""
            summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ns) or "").split())
            canonical_url = f"https://arxiv.org/abs/{arxiv_id}"
            rows.append(
                SourceRow(
                    source_ref=f"S{len(rows) + 1}",
                    title=title,
                    authors=[author for author in authors if author][:8],
                    year=int(published[:4]) if published[:4].isdigit() else None,
                    venue="arXiv",
                    doi=None,
                    arxiv_id=arxiv_id,
                    url=canonical_url,
                    abstract=summary or None,
                    source_api="arXiv",
                    external_id=arxiv_id,
                    retrieved_at=retrieved_at,
                    source_mode="arxiv_fulltext",
                    readiness_label=SOURCE_MODE_LABELS["arxiv_fulltext"],
                    selection_reason="Candidate identifier was resolved by the arXiv metadata API.",
                    provenance={
                        "sourceApi": "arXiv",
                        "externalId": arxiv_id,
                        "url": canonical_url,
                        "retrievedAt": retrieved_at,
                        "sourceMode": "arxiv_fulltext",
                    },
                )
            )
        return rows, Attempt("arXiv", query, "ok", retrieved_at, result_count=len(rows))
    except TimeoutError as exc:
        return [], Attempt("arXiv", query, "timeout", retrieved_at, error=str(exc))
    except Exception as exc:
        return [], Attempt("arXiv", query, "failed", retrieved_at, error=_safe_http_error(exc))


def retrieve_semantic_scholar_arxiv_identifiers(
    identifiers: list[str], *, timeout: float = 8.0
) -> tuple[list[SourceRow], Attempt]:
    """Resolve arXiv identifiers through Semantic Scholar when arXiv is unavailable."""
    cleaned = list(dict.fromkeys(
        identifier.strip().lower().split("v")[0]
        for identifier in identifiers
        if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", identifier.strip(), re.IGNORECASE)
    ))
    retrieved_at = utc_now()
    query = ",".join(cleaned)
    if not cleaned:
        return [], Attempt("Semantic Scholar", query, "skipped", retrieved_at, error="no valid arXiv identifiers")

    api_key = (os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY") or "").strip()
    headers = {"x-api-key": api_key} if api_key else None
    fields = "paperId,externalIds,title,abstract,authors,year,venue,url,openAccessPdf"
    rows: list[SourceRow] = []
    first_error: str | None = None
    for identifier in cleaned[:12]:
        try:
            payload = _http_json(
                f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{identifier}?{urlencode({'fields': fields})}",
                timeout=timeout,
                headers=headers,
            )
            title = str(payload.get("title") or "").strip()
            if not title:
                continue
            external_ids = payload.get("externalIds") or {}
            resolved_id = str(external_ids.get("ArXiv") or identifier).strip().split("v")[0]
            paper_id = str(payload.get("paperId") or "").strip()
            open_pdf = payload.get("openAccessPdf") or {}
            url = str(open_pdf.get("url") or payload.get("url") or f"https://arxiv.org/abs/{resolved_id}").strip()
            authors = [str(item.get("name") or "").strip() for item in payload.get("authors") or []]
            rows.append(
                SourceRow(
                    source_ref=f"S{len(rows) + 1}",
                    title=title,
                    authors=[author for author in authors if author][:8],
                    year=payload.get("year") if isinstance(payload.get("year"), int) else None,
                    venue=str(payload.get("venue") or "Semantic Scholar") or "Semantic Scholar",
                    doi=str(external_ids.get("DOI") or "").strip() or None,
                    arxiv_id=resolved_id,
                    url=url,
                    abstract=str(payload.get("abstract") or "").strip() or None,
                    source_api="Semantic Scholar",
                    external_id=paper_id or resolved_id,
                    retrieved_at=retrieved_at,
                    source_mode="abstract_available",
                    readiness_label=SOURCE_MODE_LABELS["abstract_available"],
                    selection_reason="Candidate identifier was resolved by the Semantic Scholar metadata API.",
                    provenance={
                        "sourceApi": "Semantic Scholar",
                        "externalId": paper_id or resolved_id,
                        "url": url,
                        "retrievedAt": retrieved_at,
                        "sourceMode": "abstract_available",
                    },
                )
            )
        except Exception as exc:
            first_error = first_error or _safe_http_error(exc)
    status = "ok" if rows and not first_error else "partial_error" if rows else "failed"
    return rows, Attempt("Semantic Scholar", query, status, retrieved_at, error=first_error, result_count=len(rows))


def _semantic_scholar_sources(query: str, *, limit: int, timeout: float) -> tuple[list[SourceRow], Attempt]:
    retrieved_at = utc_now()
    fields = "paperId,externalIds,title,abstract,authors,year,venue,url,openAccessPdf,isOpenAccess"
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode(
        {"query": query, "limit": str(min(limit, 100)), "fields": fields}
    )
    api_key = (os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or os.environ.get("S2_API_KEY") or "").strip()
    headers = {"x-api-key": api_key} if api_key else None
    try:
        payload = _http_json(url, timeout=timeout, headers=headers)
        rows: list[SourceRow] = []
        for idx, paper in enumerate(payload.get("data") or []):
            title = (paper.get("title") or "").strip()
            if not title:
                continue
            external_ids = paper.get("externalIds") or {}
            doi = (external_ids.get("DOI") or "").strip() or None
            arxiv_id = (external_ids.get("ArXiv") or "").strip() or None
            paper_id = (paper.get("paperId") or "").strip() or f"semantic_scholar:{idx}"
            open_pdf = paper.get("openAccessPdf") or {}
            pdf_url = open_pdf.get("url") if isinstance(open_pdf, dict) else None
            source_mode = "abstract_available" if paper.get("abstract") else "metadata_only"
            if paper.get("isOpenAccess") or pdf_url:
                source_mode = "oa_located"
            authors = [
                (author.get("name") or "").strip()
                for author in (paper.get("authors") or [])
                if isinstance(author, dict) and (author.get("name") or "").strip()
            ]
            row_url = pdf_url or paper.get("url") or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None)
            rows.append(
                SourceRow(
                    source_ref=f"S{len(rows)+1}",
                    title=title,
                    authors=authors[:8],
                    year=paper.get("year") if isinstance(paper.get("year"), int) else None,
                    venue=(paper.get("venue") or "Semantic Scholar"),
                    doi=doi,
                    arxiv_id=arxiv_id,
                    url=row_url,
                    abstract=(paper.get("abstract") or None),
                    source_api="Semantic Scholar",
                    external_id=paper_id,
                    retrieved_at=retrieved_at,
                    source_mode=source_mode,
                    readiness_label=SOURCE_MODE_LABELS[source_mode],
                    selection_reason="Title/abstract matched the query; source metadata from Semantic Scholar.",
                    provenance={
                        "sourceApi": "Semantic Scholar",
                        "query": query,
                        "externalId": paper_id,
                        "url": row_url,
                        "retrievedAt": retrieved_at,
                        "sourceMode": source_mode,
                    },
                )
            )
        return rows, Attempt("Semantic Scholar", query, "ok", retrieved_at, result_count=len(rows))
    except TimeoutError as exc:
        return [], Attempt("Semantic Scholar", query, "timeout", retrieved_at, error=str(exc))
    except Exception as exc:
        return [], Attempt("Semantic Scholar", query, "failed", retrieved_at, error=_safe_http_error(exc))


def _unpaywall_enrich(rows: list[SourceRow], query: str, *, timeout: float) -> Attempt:
    retrieved_at = utc_now()
    email = (os.environ.get("UNPAYWALL_EMAIL") or "").strip()
    dois = list(dict.fromkeys([row.doi for row in rows if row.doi]))[:8]
    if not email:
        return Attempt("Unpaywall", query, "not_configured", retrieved_at, result_count=0)
    if not dois:
        return Attempt("Unpaywall", query, "no_doi_candidates", retrieved_at, result_count=0)

    enriched = 0
    first_error: str | None = None
    by_doi = {row.doi: row for row in rows if row.doi}
    for doi in dois:
        url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?{urlencode({'email': email})}"
        try:
            payload = _http_json(url, timeout=timeout)
            row = by_doi.get(doi)
            if not row:
                continue
            oa_location = payload.get("best_oa_location") or {}
            oa_url = oa_location.get("url_for_pdf") or oa_location.get("url") or payload.get("doi_url")
            if oa_url:
                row.url = row.url or oa_url
                row.source_mode = "oa_located"
                row.readiness_label = SOURCE_MODE_LABELS["oa_located"]
                row.provenance["sourceMode"] = row.source_mode
                row.provenance["unpaywall"] = {
                    "queriedAt": retrieved_at,
                    "doi": doi,
                    "isOa": bool(payload.get("is_oa")),
                    "bestOaUrl": oa_url,
                }
                enriched += 1
        except TimeoutError as exc:
            first_error = str(exc)
        except Exception as exc:
            first_error = _safe_http_error(exc)
    if enriched and first_error:
        status = "partial_error"
    elif enriched:
        status = "ok"
    elif first_error:
        status = "no_oa_match" if first_error == "HTTPError:404" else "failed"
    else:
        status = "no_oa_match"
    return Attempt("Unpaywall", query, status, retrieved_at, error=first_error, result_count=enriched)


def run_literature_live(
    topic: str,
    *,
    top_k: int = 8,
    timeout: float = 12.0,
) -> LiteratureRun:
    """Retrieve and dedupe real sources without scientific semantic scoring."""
    strategy = build_search_strategy(topic, top_k=top_k)
    query = strategy["normalizedQuery"]
    run = LiteratureRun(
        topic=topic,
        strategy=strategy,
        key_status=configured_key_status(),
    )
    if not query:
        run.failure_state = _failure(
            "missing_topic",
            "没有收到可检索的研究问题。",
            [],
            [],
        )
        return run

    all_rows: list[SourceRow] = []
    limit = max(top_k * 2, 12)
    for fetch in (
        _openalex_sources,
        _semantic_scholar_sources,
        _arxiv_sources,
    ):
        rows, attempt = fetch(query, limit=limit, timeout=timeout)
        run.attempted_apis.append(attempt)
        all_rows.extend(rows)

    run.attempted_apis.append(
        _unpaywall_enrich(
            all_rows,
            query,
            timeout=min(timeout, 6.0),
        )
    )

    run.sources = _dedupe(all_rows)
    for idx, row in enumerate(run.sources, start=1):
        row.source_ref = f"S{idx}"
        row.score = 0.0
        row.relevance_score = 0.0
        row.matched_terms = []
        row.matched_phrases = []
        row.missing_core_terms = []
        row.relevance_reason = ""
        row.is_relevant_enough = False
        row.selection_status = "retrieved_candidate"
        row.selection_reason = (
            "真实检索候选；科研相关性、角色和优先级尚待正式 Agent 判断。"
        )
        row.exclusion_reason = None
        row.provenance["sourceRef"] = row.source_ref
        row.provenance["selectionStatus"] = row.selection_status
        row.provenance["semanticScreening"] = "pending_agent_judgment"

    run.top_k = []
    run.relevance_gate = {
        "mode": "retrieval_only",
        "semanticScreening": "pending_agent_judgment",
        "retrievalCount": run.source_count,
        "dedupedCount": run.deduped_count,
    }

    if not run.sources:
        run.failure_state = _failure(
            "no_sources",
            "外部文献 API 没有返回可用来源，或当前网络不可达。",
            run.attempted_apis,
            [],
        )
    return run



def _dedupe(rows: list[SourceRow]) -> list[SourceRow]:
    seen: set[str] = set()
    out: list[SourceRow] = []
    for row in rows:
        key = (row.doi or row.arxiv_id or row.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out










def _source_boundary(row: SourceRow) -> str:
    if row.source_mode == "arxiv_fulltext":
        return "有 arXiv 全文线索，但本轮未解析全文。"
    if row.source_mode == "oa_located":
        return "发现开放获取线索，但不等于已读全文。"
    if row.source_mode == "abstract_available":
        return "仅基于题录和摘要。"
    return "仅基于题录元数据。"


def _legacy_failure(kind: str, reason: str, attempts: list[Attempt], partial: list[SourceRow]) -> dict[str, Any]:
    statuses = {a.status for a in attempts}
    retry = "稍后重试，或缩小/改写检索词后重试。"
    if "timeout" in statuses:
        retry = "外部 API 超时；稍后重试或降低 Top-K。"
    return {
        "kind": kind,
        "reason": reason,
        "impact": "无法形成完整来源表和短综述；不会创建空结果冒充真实调研。",
        "nextAction": "补充关键词、种子论文或改写研究问题后重试。",
        "retryPolicy": retry,
        "partialOutputStatus": "保留检索策略、attempted source APIs 和任何已返回的部分来源。",
        "attemptedSourceApis": [a.__dict__ for a in attempts],
        "partialArtifacts": [source_to_dict(s) for s in partial[:5]],
    }


def source_to_dict(row: SourceRow) -> dict[str, Any]:
    return {
        "sourceRef": row.source_ref,
        "title": row.title,
        "authors": row.authors,
        "year": row.year,
        "venue": row.venue,
        "doi": row.doi,
        "arxivId": row.arxiv_id,
        "url": row.url,
        "sourceApi": row.source_api,
        "externalId": row.external_id,
        "retrievedAt": row.retrieved_at,
        "sourceMode": row.source_mode,
        "readinessLabel": row.readiness_label,
        "relevanceScore": round(row.relevance_score, 3),
        "matchedTerms": row.matched_terms,
        "matchedPhrases": row.matched_phrases,
        "missingCoreTerms": row.missing_core_terms,
        "relevanceReason": row.relevance_reason,
        "isRelevantEnough": row.is_relevant_enough,
        "selectionStatus": row.selection_status,
        "selectionReason": row.selection_reason,
        "exclusionReason": row.exclusion_reason,
        "provenance": row.provenance,
    }


def _failure(
    kind: str,
    reason: str,
    attempts: list[Attempt],
    partial: list[SourceRow],
    *,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    statuses = {attempt.status for attempt in attempts}
    retry = "补充更明确的研究问题或种子论文后重试。"
    if "timeout" in statuses:
        retry = "外部检索服务超时；稍后重试，或先缩小研究问题范围。"
    attempted_queries = list(
        dict.fromkeys(
            attempt.query
            for attempt in attempts
            if attempt.query
        )
    )
    return {
        "kind": kind,
        "state": "gap_state",
        "reason": reason,
        "impact": (
            "本轮无法形成可供正式 Agent 筛选的真实来源集合；"
            "系统不会虚构论文或用关键词规则代替科研判断。"
        ),
        "nextAction": "补充研究问题、种子论文、DOI 或 arXiv ID 后重试。",
        "retryPolicy": retry,
        "partialOutputStatus": "仅保留真实检索尝试和 provenance。",
        "attemptedQueries": attempted_queries,
        "attemptedSourceApis": [attempt.__dict__ for attempt in attempts],
        "retrievalCount": sum(attempt.result_count for attempt in attempts),
        "dedupedCount": len(partial),
        "relevantCandidateCount": 0,
        "topKRelevantCount": 0,
        "relevanceGate": gate or {},
        "userMessage": (
            "本轮没有形成可供科研筛选的真实来源集合。"
            "请补充研究问题或种子论文后重试。"
        ),
        "partialArtifacts": [source_to_dict(source) for source in partial[:5]],
    }



def markdown_artifacts(run: LiteratureRun) -> dict[str, str]:
    return {
        "search_strategy": _strategy_markdown(run),
        "source_table": _source_table_markdown(run),
        "top_k": _topk_markdown(run),
        "readiness": _readiness_markdown(run),
        "bounded_review": _review_markdown(run),
        "gaps_next": _gaps_markdown(run),
    }








def _readiness_markdown(run: LiteratureRun) -> str:
    counts: dict[str, int] = {}
    for row in run.sources:
        counts[row.readiness_label] = counts.get(row.readiness_label, 0) + 1
    lines = ["# 可读性说明", ""]
    if counts:
        lines.extend(f"- {label}: {count}" for label, count in counts.items())
    else:
        lines.append("- 本轮没有可读性可判定的来源。")
    lines.extend(
        [
            "",
            "边界：开放获取线索不等于已读全文；arXiv 全文线索不等于本轮已解析全文；题录/摘要不能支撑逐条证据结论。",
        ]
    )
    return "\n".join(lines)






def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()










# C2C user-facing polish: keep the legacy helpers above for audit history, but
# make the final definitions below the active rendering path.
def _c2c_selection_status_label(status: str) -> str:
    return "已纳入推荐" if status == "selected" else "候选来源"


def _c2c_gate_pass_label(value: Any) -> str:
    return "已通过" if value is True else "未通过"


def _c2c_attempt_status_label(attempt: Attempt) -> str:
    if attempt.source_api == "Unpaywall":
        if attempt.status == "ok":
            return "已找到开放获取线索"
        if attempt.status == "partial_error":
            return "部分开放获取线索不可用"
        if attempt.status in {"no_oa_match", "no_oa_location", "no_doi_candidates"}:
            return "未找到开放获取线索"
        if attempt.status == "not_configured":
            return "未配置开放获取查询"
    labels = {
        "ok": "已返回结果",
        "timeout": "请求超时",
        "failed": "请求失败",
        "not_configured": "未配置",
        "no_doi_candidates": "没有 DOI 候选",
    }
    return labels.get(attempt.status, attempt.status)


def _c2c_next_step_suggestions(run: LiteratureRun) -> list[str]:
    if run.failure_state:
        return [
            "补充更具体的领域关键词、种子论文、DOI 或 arXiv ID 后重试。",
            "如果问题本身是探索性的，先把研究对象、方法范围和评估任务拆成更明确的检索词。",
            "不要把本轮低相关候选当作文献调研结论使用。",
        ]
    return [
        "先人工筛读推荐阅读清单，确认是否符合当前研究问题。",
        "需要证据表或机制链时，先形成可引用的来源包，再进入下一阶段。",
        "如需提升覆盖面，可补充种子论文或领域限定词后重新检索。",
    ]


def _c2c_gap_user_message(run: LiteratureRun) -> str:
    failure = run.failure_state or {}
    gate = run.relevance_gate or failure.get("relevanceGate") or {}
    retrieval_count = int(failure.get("retrievalCount", gate.get("retrievalCount", run.source_count)) or 0)
    relevant_count = int(failure.get("relevantCandidateCount", gate.get("relevantCandidateCount", 0)) or 0)
    statuses = {attempt.status for attempt in run.attempted_apis}
    has_source_error = bool(statuses & {"failed", "timeout"})
    if retrieval_count > 0 and relevant_count <= 0:
        return (
            "本轮找到了一些外部结果，但它们和当前问题相关性太弱，不能作为有效文献调研结果。"
            "建议补充领域关键词、种子论文、DOI 或 arXiv ID 后重试。"
        )
    if retrieval_count > 0:
        return (
            "本轮找到了一些候选来源，但通过相关性筛选的数量不足，暂时不能形成可靠的推荐阅读清单。"
            "建议补充领域关键词、种子论文、DOI 或 arXiv ID 后重试。"
        )
    if has_source_error:
        return (
            "本轮没有找到可用的外部文献结果，且部分检索来源不可用。"
            "建议稍后重试，或补充领域关键词、种子论文、DOI 或 arXiv ID。"
        )
    return (
        "本轮没有找到可用的外部文献结果。"
        "建议补充领域关键词、种子论文、DOI 或 arXiv ID 后重试。"
    )

def _strategy_markdown(run: LiteratureRun) -> str:
    strategy = run.strategy
    return "\n".join(
        [
            "# 检索执行",
            "",
            f"- 原始问题：{strategy['originalTopic']}",
            f"- 实际查询式：{strategy['normalizedQuery']}",
            f"- 来源范围：{', '.join(strategy['sourcePriority'])}",
            "- 科研语义筛选：待正式 key_literature_positioning Agent 判断",
            "",
            "边界：本层只执行真实外部检索、去重和 provenance 记录；"
            "不判断科研相关性、文献角色或阅读优先级。",
        ]
    )


def _source_table_markdown(run: LiteratureRun) -> str:
    lines = [
        "# 来源表",
        "",
        "以下仅为真实检索候选，尚未做科研语义筛选或优先级排序。",
        "",
        "| Ref | 标题 | 年份 | 来源 | API | 查询式 | externalId | URL | retrievedAt | sourceMode | 可读性 | 状态 |",
        "|---|---|---:|---|---|---|---|---|---|---|---|---|",
    ]
    for row in run.sources:
        provenance = row.provenance or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    row.source_ref,
                    _cell(row.title),
                    str(row.year or ""),
                    _cell(row.venue or row.source_api),
                    row.source_api,
                    _cell(str(provenance.get("query") or "")),
                    _cell(row.external_id),
                    _cell(row.url or ""),
                    row.retrieved_at,
                    row.source_mode,
                    row.readiness_label,
                    "待 Agent 科研筛选",
                ]
            )
            + " |"
        )
    if not run.sources:
        lines.append(
            "| - | 未返回可用来源 | - | - | - | - | - | - | - | - | 受阻 | 无候选 |"
        )
    lines.extend(["", "## 完整 provenance", ""])
    for row in run.sources:
        provenance = row.provenance or {}
        lines.append(
            f"- {row.source_ref}: sourceApi={row.source_api}; "
            f"query=`{provenance.get('query') or ''}`; "
            f"externalId={row.external_id}; url={row.url or 'n/a'}; "
            f"retrievedAt={row.retrieved_at}; sourceMode={row.source_mode}; "
            f"readinessLabel={row.readiness_label}; "
            f"semanticScreening=pending_agent_judgment."
        )
    return "\n".join(lines)


def _topk_markdown(run: LiteratureRun) -> str:
    lines = [
        "# 候选来源清单（待科研筛选）",
        "",
        "本层不生成 Top-K 科研推荐。以下仅按真实检索返回顺序展示候选，"
        "后续由正式 key_literature_positioning Agent 判断相关性和阅读优先级。",
        "",
    ]
    for row in run.sources[:8]:
        lines.extend(
            [
                f"## {row.source_ref}. {row.title}",
                f"- 来源：{row.source_api}",
                f"- 可读性：{row.readiness_label}",
                f"- sourceMode：{row.source_mode}",
                "",
            ]
        )
    if not run.sources:
        lines.append("本轮没有真实检索候选。")
    return "\n".join(lines)


def _review_markdown(run: LiteratureRun) -> str:
    lines = [
        "# 候选摘要摘录（未做科研综合）",
        "",
        "以下只摘录真实检索结果中的题名/摘要，代码不形成科研综述、"
        "相关性判断或机制结论。",
        "",
    ]
    rows = [row for row in run.sources[:8] if row.abstract]
    for row in rows[:5]:
        excerpt = (row.abstract or "").strip()
        if len(excerpt) > 220:
            excerpt = excerpt[:220].rstrip() + "..."
        lines.append(f"- {row.source_ref}《{row.title}》：{excerpt}")
    if not rows:
        lines.append("当前候选缺少可用摘要；仅保留题录与 provenance。")
    return "\n".join(lines)


def _gaps_markdown(run: LiteratureRun) -> str:
    lines = ["# 检索边界与下一步", ""]
    if run.failure_state:
        lines.extend(
            [
                f"- reason: {run.failure_state['reason']}",
                f"- impact: {run.failure_state['impact']}",
                f"- nextAction: {run.failure_state['nextAction']}",
                "",
            ]
        )
    lines.extend(
        [
            f"- 本轮检索结果数量：{run.source_count}",
            f"- 去重后数量：{run.deduped_count}",
            "- 科研相关性/角色/优先级：当前检索层未判断",
            "",
            "## 下一步建议",
            "- 将真实候选交给正式 key_literature_positioning Agent 做科研筛选。",
            "- 需要证据表或机制链时，再对 Agent 确认的来源做证据抽取。",
            "",
            "## 检索来源状态",
        ]
    )
    for attempt in run.attempted_apis:
        error_note = f"；错误：{attempt.error}" if attempt.error else ""
        lines.append(
            f"- {attempt.source_api}：status={attempt.status}；"
            f"query=`{attempt.query}`；返回 {attempt.result_count} 条；"
            f"retrievedAt={attempt.retrieved_at}{error_note}"
        )
    return "\n".join(lines)

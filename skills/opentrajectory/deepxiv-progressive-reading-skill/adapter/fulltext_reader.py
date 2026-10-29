"""Non-arXiv full-text reader for P0.7b progressive reading.

Provides a degradation chain to fetch full text for papers that lack
an arXiv ID:

    1. Unpaywall API  →  OA HTML  →  BeautifulSoup extraction
    2. Unpaywall API  →  OA PDF   →  PyMuPDF extraction
    3. Candidate's own pdf_url / source_url  →  PyMuPDF
    4. Candidate's landing_page_url  →  HTML extraction
    5. All fail  →  return None (caller falls back to abstract)

Every step records diagnostics; individual failures never block the
batch.  HTTP and PDF parsing are injected via Protocol interfaces so
that unit tests can run without real network or library dependencies.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_TEXT_LENGTH = 200
_UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
_DEFAULT_TIMEOUT = 30.0
_UNPAYWALL_TIMEOUT = 15.0
_MAX_HTML_BYTES = 2 * 1024 * 1024
_MAX_PDF_BYTES = 20 * 1024 * 1024
_MAX_PDF_PAGES = 20
_USER_AGENT = (
    "OpenAIR-OpenTrajectory/0.1 "
    "(academic OA metadata/fulltext validation; public OA URLs only)"
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FulltextResult:
    """Successful full-text extraction result."""

    text: str
    source_url: str
    reading_mode: str  # "html_fulltext" | "pdf_fulltext"
    content_hash: str
    parser_used: str  # "beautifulsoup4" | "pymupdf"
    acquisition_provider: str = ""
    material_type: str = "article_fulltext"
    diagnostics: dict[str, Any] = field(default_factory=dict)
    tables: tuple[dict[str, Any], ...] = ()
    # Identity observed in provider-returned metadata/content. Never copied from the request.
    provider_observed_title: str = ""
    provider_observed_identity: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Dependency-injection protocols
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HttpResponse:
    """Minimal HTTP response container."""

    status_code: int
    text: str
    content: bytes
    headers: dict[str, str]
    url: str


@runtime_checkable
class HttpClient(Protocol):
    """HTTP client protocol — production uses httpx, tests use fakes."""

    def get(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        follow_redirects: bool = True,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> HttpResponse: ...


@runtime_checkable
class PdfParser(Protocol):
    """PDF parser protocol — production uses PyMuPDF, tests use fakes."""

    def extract_text(self, pdf_bytes: bytes) -> str: ...


# ---------------------------------------------------------------------------
# Production implementations
# ---------------------------------------------------------------------------

class HttpxClient:
    """httpx-based HTTP client (lazy import)."""

    def get(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        follow_redirects: bool = True,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> HttpResponse:
        import httpx

        request_headers = dict(headers or {})
        request_headers.setdefault("User-Agent", _USER_AGENT)
        with httpx.stream(
            "GET",
            url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers=request_headers,
        ) as resp:
            response_headers = dict(resp.headers)
            if max_bytes is not None and _content_length_exceeds(
                response_headers, max_bytes
            ):
                raise ContentTooLargeError(
                    f"content-length exceeds limit={max_bytes}"
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ContentTooLargeError(
                        f"streamed response exceeds limit={max_bytes}"
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
            encoding = resp.encoding or "utf-8"
            text = content.decode(encoding, errors="replace")
            final_url = str(resp.url)
            status_code = resp.status_code
        return HttpResponse(
            status_code=status_code,
            text=text,
            content=content,
            headers=response_headers,
            url=final_url,
        )


class PyMuPDFParser:
    """PyMuPDF (fitz) PDF text extractor (lazy import)."""

    def extract_text(self, pdf_bytes: bytes, *, max_pages: int = _MAX_PDF_PAGES) -> str:
        if not pdf_bytes:
            return ""
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages: list[str] = []
            page_limit = max(1, int(max_pages))
            for page in doc[:page_limit]:
                text = page.get_text("text")
                if text:
                    pages.append(text)
            doc.close()
            return "\n\n".join(pages)
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Unpaywall client
# ---------------------------------------------------------------------------

class ContentTooLargeError(RuntimeError):
    """Raised when a response exceeds the configured byte budget."""


def _is_public_http_url(url: str) -> bool:
    """Allow only public http(s) URLs with non-localhost hosts."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _headers_with_user_agent(headers: dict[str, str] | None = None) -> dict[str, str]:
    out = dict(headers or {})
    out.setdefault("User-Agent", _USER_AGENT)
    return out


def _header_value(headers: dict[str, str], name: str) -> str:
    lname = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == lname:
            return str(value)
    return ""


def _content_length_exceeds(headers: dict[str, str], max_bytes: int) -> bool:
    raw = _header_value(headers, "content-length")
    if not raw:
        return False
    try:
        return int(raw.strip()) > max_bytes
    except ValueError:
        return False


def _get_limited(
    http_client: HttpClient,
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_retries: int = 0,
) -> HttpResponse:
    """Bounded GET. Retries only transient transport/429/5xx failures."""
    if not _is_public_http_url(url):
        raise ValueError("blocked_non_public_or_invalid_url")
    attempts = max(1, min(3, int(max_retries) + 1))
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = http_client.get(
                url, timeout=timeout, headers=_headers_with_user_agent(), max_bytes=max_bytes
            )
            if _content_length_exceeds(resp.headers, max_bytes):
                raise ContentTooLargeError(f"content-length exceeds limit={max_bytes}")
            if len(resp.content or b"") > max_bytes:
                raise ContentTooLargeError(f"response body exceeds limit={max_bytes}")
            if resp.status_code == 429 or 500 <= resp.status_code <= 599:
                if attempt + 1 < attempts:
                    continue
            return resp
        except ContentTooLargeError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
    assert last_exc is not None
    raise last_exc


def lookup_unpaywall(
    doi: str,
    *,
    email: str,
    http_client: HttpClient,
    timeout: float = _UNPAYWALL_TIMEOUT,
    max_retries: int = 1,
) -> dict[str, Any] | None:
    """Query Unpaywall API for open-access locations.

    Returns the JSON response dict on success, or ``None`` on any
    failure (404, timeout, network error, missing email).
    """
    if not doi or not email:
        return None
    import json as _json

    url = f"{_UNPAYWALL_BASE}/{doi}?email={email}"
    try:
        resp = _get_limited(
            http_client,
            url,
            timeout=timeout,
            max_bytes=_MAX_HTML_BYTES,
            max_retries=max_retries,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        return _json.loads(resp.text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTML text extraction
# ---------------------------------------------------------------------------

_STRIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript"}


def extract_text_from_html(html: str) -> str:
    """Extract article body text from publisher HTML.

    Uses BeautifulSoup4 with the built-in ``html.parser``.
    Falls back through ``<article>`` → ``<main>`` → ``<body>``.
    """
    if not html or not html.strip():
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _extract_text_from_html_stdlib(html)

    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements.
    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Try semantic containers in priority order.
    for container_tag in ("article", "main"):
        container = soup.find(container_tag)
        if container:
            text = container.get_text(separator="\n", strip=True)
            if len(text) >= _MIN_TEXT_LENGTH:
                return _normalize_whitespace(text)

    # Try common class/id patterns.
    for selector in (
        "div.article-body",
        "div.article-content",
        "div#content",
        "div.content",
        "div.full-text",
    ):
        container = soup.select_one(selector)
        if container:
            text = container.get_text(separator="\n", strip=True)
            if len(text) >= _MIN_TEXT_LENGTH:
                return _normalize_whitespace(text)

    # Final fallback: body.
    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)
    return _normalize_whitespace(text)


def extract_tables_from_html(html: str) -> list[dict[str, Any]]:
    """Extract structured HTML tables (full page or embedded fragments).

    Preserves caption, header rows, and cell grid. Mentions of "Table N"
    without a real ``<table><tr>…`` block are not counted as tables.
    """
    if not html or "<table" not in html.casefold():
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _extract_tables_from_html_stdlib(html)

    soup = BeautifulSoup(html, "html.parser")
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(soup.find_all("table"), start=1):
        caption_node = table.find("caption")
        caption = caption_node.get_text(" ", strip=True) if caption_node else ""
        rows: list[list[str]] = []
        header_rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in tr.find_all(["th", "td"], recursive=False)
            ]
            if not cells:
                cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            cells = [re.sub(r"\s+", " ", cell).strip() for cell in cells]
            if not any(cells):
                continue
            rows.append(cells)
            if tr.find("th") is not None:
                header_rows.append(cells)
        if not rows or max(len(row) for row in rows) < 2:
            continue
        tables.append({
            "table_index": index,
            "caption": caption[:240],
            "headers": header_rows[:3],
            "rows": rows[:80],
            "row_count": len(rows),
            "column_count": max(len(row) for row in rows),
        })
        if len(tables) >= 40:
            break
    return tables


def extract_embedded_html_tables(text: str) -> list[dict[str, Any]]:
    """Find ``<table>…</table>`` blocks inside markdown/plain DeepXiv bodies."""
    if not text or "<table" not in text.casefold():
        return []
    lowered = text.lstrip().casefold()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html") or "<body" in lowered[:2000]:
        return extract_tables_from_html(text)
    fragments = re.findall(r"(?is)<table\b.*?</table>", text)
    tables: list[dict[str, Any]] = []
    for fragment in fragments:
        if not re.search(r"(?is)<tr\b", fragment):
            continue
        for table in extract_tables_from_html(fragment):
            table = dict(table)
            table["table_index"] = len(tables) + 1
            tables.append(table)
            if len(tables) >= 40:
                return tables
    return tables


def table_reference_count(text: str) -> int:
    return len(re.findall(r"\btable\s*\d+\b", text or "", flags=re.I))


def _extract_tables_from_html_stdlib(html: str) -> list[dict[str, Any]]:
    """Stdlib fallback that recovers basic row/cell grids from HTML tables."""
    from html.parser import HTMLParser

    class _TableParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.tables: list[dict[str, Any]] = []
            self._current: dict[str, Any] | None = None
            self._row: list[str] | None = None
            self._cell: list[str] | None = None
            self._in_caption = False
            self._caption: list[str] = []
            self._row_has_header = False

        def handle_starttag(self, tag: str, attrs: list) -> None:
            tag = tag.lower()
            if tag == "table":
                self._current = {"caption": "", "rows": [], "headers": []}
            elif tag == "caption" and self._current is not None:
                self._in_caption = True
                self._caption = []
            elif tag == "tr" and self._current is not None:
                self._row = []
                self._row_has_header = False
            elif tag in {"td", "th"} and self._row is not None:
                self._cell = []
                self._row_has_header = self._row_has_header or tag == "th"

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            if tag in {"td", "th"} and self._cell is not None and self._row is not None:
                self._row.append(re.sub(r"\s+", " ", " ".join(self._cell)).strip())
                self._cell = None
            elif tag == "tr" and self._row is not None and self._current is not None:
                if any(self._row):
                    self._current["rows"].append(self._row)
                    if self._row_has_header:
                        self._current["headers"].append(self._row)
                self._row = None
            elif tag == "caption" and self._current is not None:
                self._current["caption"] = re.sub(r"\s+", " ", " ".join(self._caption)).strip()
                self._in_caption = False
            elif tag == "table" and self._current is not None:
                rows = self._current["rows"]
                if rows and max(len(row) for row in rows) >= 2:
                    self.tables.append({
                        "table_index": len(self.tables) + 1,
                        "caption": self._current["caption"][:240],
                        "headers": self._current["headers"][:3],
                        "rows": rows[:80],
                        "row_count": len(rows),
                        "column_count": max(len(row) for row in rows),
                    })
                self._current = None

        def handle_data(self, data: str) -> None:
            if self._cell is not None and data.strip():
                self._cell.append(data.strip())
            if self._in_caption and data.strip():
                self._caption.append(data.strip())

    parser = _TableParser()
    parser.feed(html)
    return parser.tables[:40]


def _extract_text_from_html_stdlib(html: str) -> str:
    """Fallback HTML extraction using only the stdlib (no BS4)."""
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._skip = False
            self._parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag.lower() in _STRIP_TAGS:
                self._skip = True

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() in _STRIP_TAGS:
                self._skip = False

        def handle_data(self, data: str) -> None:
            if not self._skip and data.strip():
                self._parts.append(data.strip())

    parser = _TextExtractor()
    parser.feed(html)
    return _normalize_whitespace("\n".join(parser._parts))


def _extract_html_observed_title(html: str) -> str:
    """Best-effort title from publisher HTML meta tags (not the candidate claim)."""
    if not html:
        return ""
    patterns = (
        r'<meta[^>]+name=["\']citation_title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_title["\']',
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r"<title[^>]*>([^<]+)</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            title = _normalize_whitespace(match.group(1))
            if title:
                return title
    return ""



_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
_ARXIV_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5}(?:v\d+)?)(?!\d)", re.I)
_SUPPLEMENT_MARKERS = (
    "supplementary information", "supplementary material",
    "supporting information", "supplemental information", "supplemental material",
)


def _normalize_doi(value: str) -> str:
    doi = str(value or "").strip().casefold()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.rstrip("./")


def _extract_html_observed_identity(html: str) -> dict[str, str]:
    """Extract identifiers actually present in returned publisher HTML."""
    observed: dict[str, str] = {}
    for name in ("citation_doi", "dc.identifier", "dc.identifier.doi"):
        patterns = (
            rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, html or "", flags=re.I)
            if match:
                doi_match = _DOI_RE.search(match.group(1))
                if doi_match:
                    observed["doi"] = _normalize_doi(doi_match.group(0))
                    break
        if observed.get("doi"):
            break
    for name in ("citation_arxiv_id", "arxiv_id"):
        patterns = (
            rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, html or "", flags=re.I)
            if match:
                aid = _ARXIV_RE.search(match.group(1))
                if aid:
                    observed["arxiv_id"] = aid.group(1)
                    break
        if observed.get("arxiv_id"):
            break
    return observed


class ProviderIdentityConflict(ValueError):
    pass


def _merge_observed_identity(*values: dict[str, Any] | None) -> dict[str, str] | None:
    merged: dict[str, str] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key in ("doi", "arxiv_id", "pmcid", "openalex_id"):
            raw = str(value.get(key) or "").strip()
            if not raw:
                continue
            normalized = _normalize_doi(raw) if key == "doi" else raw
            if merged.get(key) and merged[key].casefold() != normalized.casefold():
                raise ProviderIdentityConflict(f"{key}_conflict")
            merged[key] = normalized
    return merged or None


def _supplement_in_front_matter(text: str) -> bool:
    lines = [re.sub(r"\s+", " ", line).strip().casefold() for line in (text or "").splitlines()[:16]]
    for line in lines:
        if not line or len(line) > 180:
            continue
        if any(line.startswith(marker) or line == marker for marker in _SUPPLEMENT_MARKERS):
            return True
    return False


def _classify_material_type(text: str, *, source_url: str = "", html: str = "") -> str:
    """Preserve material type; supplementary and wrapper pages are never article body."""
    folded_url = (source_url or "").casefold()
    folded_head = (text or "")[:1200].casefold()
    html_head = (html or "")[:12000].casefold()
    if any(marker.replace(" ", "") in re.sub(r"[^a-z]", "", folded_url) for marker in _SUPPLEMENT_MARKERS):
        return "supplementary_material"
    if any(folded_head.lstrip().startswith(marker) for marker in _SUPPLEMENT_MARKERS) or _supplement_in_front_matter(text):
        return "supplementary_material"
    if any(marker in html_head for marker in ("access denied", "sign in", "log in", "captcha", "page not found", "service unavailable")):
        return "landing_page"
    return "article_fulltext"


def _html_is_scientific_article(html: str, text: str) -> bool:
    """Reject landing/login/error/abstract-only pages before material admission."""
    folded = (html or "").casefold()
    text_folded = (text or "").casefold()
    bad = ("access denied", "sign in", "log in", "captcha", "page not found", "404", "service unavailable")
    if any(token in folded[:12000] or token in text_folded[:4000] for token in bad):
        return False
    fulltext_terms = ("methods", "materials and methods", "experimental", "results", "discussion", "conclusion")
    structural_hits = sum(1 for term in fulltext_terms if re.search(rf"<(?:h[1-6]|section)[^>]*>[^<]*{re.escape(term)}", folded))
    citation_meta = "citation_title" in folded
    article_container = "<article" in folded or "role=\"main\"" in folded or "<main" in folded
    abstract_only = "abstract" in text_folded and structural_hits == 0 and len(text) < 5000
    if abstract_only:
        return False
    return structural_hits >= 1 or (citation_meta and article_container and len(text) >= 1200)


def _html_fulltext_result(
    text: str,
    source_url: str,
    html: str,
    diagnostics: list[dict[str, Any]],
    *,
    provider_observed_identity: dict[str, Any] | None = None,
    acquisition_provider: str = "html",
    material_type: str = "article_fulltext",
) -> FulltextResult:
    tables = tuple(extract_tables_from_html(html))
    refs = table_reference_count(text)
    observed_title = _extract_html_observed_title(html)
    # Only document-scoped/provider metadata can establish identity.  DOI/arXiv
    # strings in article text may belong to references and are never identity proof.
    observed_identity = _merge_observed_identity(
        provider_observed_identity, _extract_html_observed_identity(html)
    )
    return FulltextResult(
        text=text,
        source_url=source_url,
        reading_mode="html_fulltext",
        content_hash=_sha256_text(text),
        parser_used="beautifulsoup4",
        acquisition_provider=acquisition_provider,
        material_type=material_type,
        diagnostics={
            "steps": list(diagnostics),
            "extractable_table_count": len(tables),
            "table_reference_count": refs,
            "table_mentioned_but_unavailable": bool(refs and not tables),
        },
        tables=tables,
        provider_observed_title=observed_title,
        provider_observed_identity=observed_identity,
    )


def _pdf_fulltext_result(
    text: str,
    source_url: str,
    diagnostics: list[dict[str, Any]],
    *,
    provider_observed_identity: dict[str, Any] | None = None,
    acquisition_provider: str = "pdf",
    material_type: str = "article_fulltext",
) -> FulltextResult:
    # Extracted PDF text is scientific content, not document identity metadata.
    # References can contain the requested DOI/arXiv ID, so identity must come
    # from a provider-scoped metadata source supplied by the acquisition path.
    observed_identity = _merge_observed_identity(provider_observed_identity)
    return FulltextResult(
        text=text,
        source_url=source_url,
        reading_mode="pdf_fulltext",
        content_hash=_sha256_text(text),
        parser_used="pymupdf",
        acquisition_provider=acquisition_provider,
        material_type=material_type,
        diagnostics={"steps": list(diagnostics)},
        provider_observed_identity=observed_identity,
    )

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace while preserving paragraph breaks."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _looks_like_pdf_url(url: str) -> bool:
    lower = url.lower()
    return lower.endswith(".pdf") or "/pdf/" in lower


def _looks_like_pdf_bytes(data: bytes) -> bool:
    return bool(data) and data[:5] == b"%PDF-"


def _is_html_response(resp: HttpResponse) -> bool:
    ct = resp.headers.get("content-type", "").lower()
    return "text/html" in ct or "application/xhtml" in ct


def _candidate_doi(candidate: dict[str, Any]) -> str:
    """Extract DOI from a candidate dict."""
    doi = candidate.get("doi")
    if isinstance(doi, str) and doi.strip():
        return doi.strip()
    ext_ids = candidate.get("external_ids")
    if isinstance(ext_ids, dict):
        doi = ext_ids.get("doi")
        if isinstance(doi, str) and doi.strip():
            return doi.strip()
    return ""


# ---------------------------------------------------------------------------
# Degradation chain: main entry point
# ---------------------------------------------------------------------------

def read_non_arxiv_fulltext(
    candidate: dict[str, Any],
    *,
    http_client: HttpClient,
    pdf_parser: PdfParser,
    unpaywall_email: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    diagnostics_out: list[dict[str, Any]] | None = None,
    allow_pdf_fulltext: bool = True,
    max_retries: int = 1,
) -> FulltextResult | None:
    """Acquire non-arXiv material while preserving provider identity/type.

    Provider fallback is bounded.  A fetched wrapper/supplement is retained as
    the terminal observed material only after all article-fulltext strategies
    have been exhausted; the caller's material admission then rejects it.
    """
    diagnostics: list[dict[str, Any]] = diagnostics_out if diagnostics_out is not None else []
    diagnostics.clear()
    doi = _candidate_doi(candidate)
    last_observed: FulltextResult | None = None

    oa_data: dict[str, Any] | None = None
    if doi and unpaywall_email:
        oa_data = lookup_unpaywall(
            doi,
            email=unpaywall_email,
            http_client=http_client,
            timeout=min(timeout, _UNPAYWALL_TIMEOUT),
            max_retries=max_retries,
        )
        if oa_data is None:
            diagnostics.append({"step": "unpaywall_lookup", "status": "failed"})
    elif not unpaywall_email:
        diagnostics.append({"step": "unpaywall_lookup", "status": "skipped", "reason": "email_not_configured"})

    best_oa: dict[str, Any] = {}
    oa_observed: dict[str, Any] | None = None
    if isinstance(oa_data, dict):
        loc = oa_data.get("best_oa_location")
        if isinstance(loc, dict):
            best_oa = loc
        # This is provider-returned Unpaywall metadata, not the requested DOI.
        observed_doi = oa_data.get("doi")
        oa_observed = {"doi": observed_doi} if observed_doi else None

    html_url = best_oa.get("url") or best_oa.get("url_for_landing_page") or ""
    if isinstance(html_url, str) and html_url:
        try:
            resp = _get_limited(http_client, html_url, timeout=timeout, max_bytes=_MAX_HTML_BYTES, max_retries=max_retries)
            if resp.status_code == 200 and _is_html_response(resp):
                text = extract_text_from_html(resp.text)
                material_type = _classify_material_type(text, source_url=resp.url, html=resp.text)
                if len(text) >= _MIN_TEXT_LENGTH and _html_is_scientific_article(resp.text, text) and material_type == "article_fulltext":
                    diagnostics.append({"step": "unpaywall_html", "status": "success", "url": resp.url, "material_type": material_type})
                    return _html_fulltext_result(text, resp.url, resp.text, diagnostics,
                        provider_observed_identity=oa_observed, acquisition_provider="unpaywall_html", material_type=material_type)
                if len(text) >= _MIN_TEXT_LENGTH:
                    material_type = material_type if material_type != "article_fulltext" else "landing_page"
                    diagnostics.append({"step": "unpaywall_html", "status": "not_admissible", "url": resp.url, "material_type": material_type})
                    last_observed = _html_fulltext_result(text, resp.url, resp.text, diagnostics,
                        provider_observed_identity=oa_observed, acquisition_provider="unpaywall_html", material_type=material_type)
                else:
                    diagnostics.append({"step": "unpaywall_html", "status": "text_too_short", "length": len(text)})
            else:
                diagnostics.append({"step": "unpaywall_html", "status": "http_error", "code": resp.status_code})
        except Exception as exc:
            diagnostics.append({"step": "unpaywall_html", "status": "exception", "error": str(exc)})

    pdf_url = best_oa.get("url_for_pdf") or ""
    if isinstance(pdf_url, str) and pdf_url:
        if not allow_pdf_fulltext:
            diagnostics.append({"step": "unpaywall_pdf", "status": "skipped", "reason": "pdf_disabled_by_policy"})
        else:
            try:
                resp = _get_limited(http_client, pdf_url, timeout=timeout, max_bytes=_MAX_PDF_BYTES, max_retries=max_retries)
                if resp.status_code == 200 and _looks_like_pdf_bytes(resp.content):
                    text = pdf_parser.extract_text(resp.content, max_pages=_MAX_PDF_PAGES)
                    if len(text) >= _MIN_TEXT_LENGTH:
                        material_type = _classify_material_type(text, source_url=resp.url or pdf_url)
                        diagnostics.append({"step": "unpaywall_pdf", "status": "success" if material_type == "article_fulltext" else "not_admissible", "url": resp.url or pdf_url, "material_type": material_type})
                        result = _pdf_fulltext_result(text, resp.url or pdf_url, diagnostics,
                            provider_observed_identity=oa_observed, acquisition_provider="unpaywall_pdf", material_type=material_type)
                        if material_type == "article_fulltext":
                            return result
                        last_observed = result
                    else:
                        diagnostics.append({"step": "unpaywall_pdf", "status": "text_too_short", "length": len(text)})
                else:
                    diagnostics.append({"step": "unpaywall_pdf", "status": "malformed_or_http_error", "code": resp.status_code})
            except Exception as exc:
                diagnostics.append({"step": "unpaywall_pdf", "status": "exception", "error": str(exc)})

    for url_key in ("pdf_url", "source_url"):
        direct_url = candidate.get(url_key, "")
        if not isinstance(direct_url, str) or not direct_url.strip() or not _looks_like_pdf_url(direct_url.strip()):
            continue
        direct_url = direct_url.strip()
        if not allow_pdf_fulltext:
            diagnostics.append({"step": "direct_pdf", "status": "skipped", "reason": "pdf_disabled_by_policy", "url_key": url_key})
            continue
        try:
            resp = _get_limited(http_client, direct_url, timeout=timeout, max_bytes=_MAX_PDF_BYTES, max_retries=max_retries)
            if resp.status_code == 200 and _looks_like_pdf_bytes(resp.content):
                text = pdf_parser.extract_text(resp.content, max_pages=_MAX_PDF_PAGES)
                if len(text) >= _MIN_TEXT_LENGTH:
                    material_type = _classify_material_type(text, source_url=resp.url or direct_url)
                    diagnostics.append({"step": "direct_pdf", "status": "success" if material_type == "article_fulltext" else "not_admissible", "url": resp.url or direct_url, "material_type": material_type})
                    result = _pdf_fulltext_result(text, resp.url or direct_url, diagnostics,
                        acquisition_provider="direct_pdf", material_type=material_type)
                    if material_type == "article_fulltext":
                        return result
                    last_observed = result
                else:
                    diagnostics.append({"step": "direct_pdf", "status": "text_too_short", "length": len(text)})
            else:
                diagnostics.append({"step": "direct_pdf", "status": "malformed_or_http_error", "code": resp.status_code})
        except Exception as exc:
            diagnostics.append({"step": "direct_pdf", "status": "exception", "error": str(exc)})

    landing = candidate.get("landing_page_url", "")
    if isinstance(landing, str) and landing.strip():
        try:
            resp = _get_limited(http_client, landing.strip(), timeout=timeout, max_bytes=_MAX_HTML_BYTES, max_retries=max_retries)
            if resp.status_code == 200 and _is_html_response(resp):
                text = extract_text_from_html(resp.text)
                material_type = _classify_material_type(text, source_url=resp.url or landing.strip(), html=resp.text)
                if len(text) >= _MIN_TEXT_LENGTH and _html_is_scientific_article(resp.text, text) and material_type == "article_fulltext":
                    diagnostics.append({"step": "landing_page_html", "status": "success", "url": resp.url or landing.strip(), "material_type": material_type})
                    return _html_fulltext_result(text, resp.url or landing.strip(), resp.text, diagnostics,
                        acquisition_provider="landing_page_html", material_type=material_type)
                if len(text) >= _MIN_TEXT_LENGTH:
                    material_type = material_type if material_type != "article_fulltext" else "landing_page"
                    diagnostics.append({"step": "landing_page_html", "status": "not_admissible", "url": resp.url or landing.strip(), "material_type": material_type})
                    last_observed = _html_fulltext_result(text, resp.url or landing.strip(), resp.text, diagnostics,
                        acquisition_provider="landing_page_html", material_type=material_type)
                else:
                    diagnostics.append({"step": "landing_page_html", "status": "text_too_short", "length": len(text)})
            else:
                diagnostics.append({"step": "landing_page_html", "status": "http_error", "code": resp.status_code})
        except Exception as exc:
            diagnostics.append({"step": "landing_page_html", "status": "exception", "error": str(exc)})

    diagnostics.append({"step": "final", "status": "all_article_fulltext_strategies_failed"})
    return last_observed


# ---------------------------------------------------------------------------
# FulltextReader facade
# ---------------------------------------------------------------------------

class FulltextReader:
    """Facade that wraps the degradation chain into a single ``read()`` call."""

    def __init__(
        self,
        *,
        http_client: HttpClient,
        pdf_parser: PdfParser,
        unpaywall_email: str | None = None,
        allow_pdf_fulltext: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = 1,
    ):
        self._http = http_client
        self._pdf = pdf_parser
        self._email = unpaywall_email
        self._allow_pdf_fulltext = bool(allow_pdf_fulltext)
        self._timeout = max(1.0, min(60.0, float(timeout)))
        self._max_retries = max(0, min(2, int(max_retries)))
        self.last_diagnostics: list[dict[str, Any]] = []

    def read(self, candidate: dict[str, Any]) -> FulltextResult | None:
        self.last_diagnostics = []
        return read_non_arxiv_fulltext(
            candidate,
            http_client=self._http,
            pdf_parser=self._pdf,
            unpaywall_email=self._email,
            diagnostics_out=self.last_diagnostics,
            allow_pdf_fulltext=self._allow_pdf_fulltext,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )

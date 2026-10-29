"""Unit tests for fulltext_reader — non-arXiv full-text retrieval.

All tests use FakeHttpClient + FakePdfParser so no real network or
third-party libraries are required.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Load module under test
# ---------------------------------------------------------------------------

_SKILL_DIR = Path(__file__).resolve().parents[1]
_ADAPTER = _SKILL_DIR / "adapter" / "fulltext_reader.py"


def _load_module():
    import sys
    spec = importlib.util.spec_from_file_location("fulltext_reader", _ADAPTER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fulltext_reader"] = mod
    spec.loader.exec_module(mod)
    return mod


fr = _load_module()

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_PDF_MAGIC = b"%PDF-1.4 fake"


@dataclass
class FakeHttpResponse:
    status_code: int = 200
    text: str = ""
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=lambda: {"content-type": "text/html"})
    url: str = "https://example.com"


class FakeHttpClient:
    """Records calls and returns pre-configured responses keyed by URL prefix."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._routes: dict[str, FakeHttpResponse] = {}
        self._default = FakeHttpResponse(status_code=404)

    def route(self, url_prefix: str, resp: FakeHttpResponse) -> None:
        self._routes[url_prefix] = resp

    def get(self, url, *, timeout=30.0, follow_redirects=True, headers=None, max_bytes=None):
        self.calls.append((url, {"timeout": timeout, "headers": headers, "max_bytes": max_bytes}))
        for prefix, resp in self._routes.items():
            if url.startswith(prefix):
                if max_bytes is not None and len(resp.content or b"") > max_bytes:
                    raise fr.ContentTooLargeError("fake streamed response exceeds limit")
                if resp.url == "https://example.com":
                    return FakeHttpResponse(status_code=resp.status_code, text=resp.text, content=resp.content, headers=resp.headers, url=url)
                return resp
        return self._default


class FakePdfParser:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self.calls: list[dict[str, int]] = []

    def extract_text(self, pdf_bytes: bytes, *, max_pages=20) -> str:
        self.calls.append({"bytes": len(pdf_bytes), "max_pages": max_pages})
        return self._text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LONG_TEXT = "A" * 250  # > _MIN_TEXT_LENGTH (200)
_SHORT_TEXT = "short"

_ARTICLE_HTML = f"""<html><head><meta name="citation_title" content="A General Study of Catalysis"></head><body>
<article><h2>Methods</h2><p>{"B" * 1250}</p></article>
</body></html>"""

_ARTICLE_HTML_WITH_NOISE = f"""<html><head><meta name="citation_title" content="A General Study of Catalysis"></head><body>
<nav>navigation bar</nav>
<script>var x = 1;</script>
<style>.foo {{}}</style>
<article><h2>Results</h2><p>{"C" * 1250}</p></article>
<footer>footer text</footer>
</body></html>"""

_BODY_ONLY_HTML = f"<html><body><p>{'D' * 250}</p></body></html>"

_TINY_HTML = "<html><body><p>hi</p></body></html>"


def _unpaywall_ok(html_url="", pdf_url=""):
    return {
        "best_oa_location": {
            "url": html_url,
            "url_for_pdf": pdf_url,
            "url_for_landing_page": "",
        }
    }


def _candidate(doi="", pdf_url="", source_url="", landing_page_url=""):
    c: dict[str, Any] = {"candidate_id": "test-1"}
    if doi:
        c["doi"] = doi
    if pdf_url:
        c["pdf_url"] = pdf_url
    if source_url:
        c["source_url"] = source_url
    if landing_page_url:
        c["landing_page_url"] = landing_page_url
    return c


# ===========================================================================
# Tests: lookup_unpaywall
# ===========================================================================


class TestLookupUnpaywall:
    def test_success(self):
        http = FakeHttpClient()
        data = _unpaywall_ok(html_url="https://oa.example/article.html")
        http.route(
            "https://api.unpaywall.org/v2/",
            FakeHttpResponse(status_code=200, text=json.dumps(data)),
        )
        result = fr.lookup_unpaywall("10.1234/test", email="me@test.com", http_client=http)
        assert result is not None
        assert result["best_oa_location"]["url"] == "https://oa.example/article.html"

    def test_404_returns_none(self):
        http = FakeHttpClient()  # default 404
        result = fr.lookup_unpaywall("10.1234/test", email="me@test.com", http_client=http)
        assert result is None

    def test_exception_returns_none(self):
        class BoomHttp:
            def get(self, *a, **kw):
                raise ConnectionError("network down")

        result = fr.lookup_unpaywall("10.1234/test", email="me@test.com", http_client=BoomHttp())
        assert result is None

    def test_missing_email_returns_none(self):
        http = FakeHttpClient()
        result = fr.lookup_unpaywall("10.1234/test", email="", http_client=http)
        assert result is None
        assert len(http.calls) == 0

    def test_missing_doi_returns_none(self):
        http = FakeHttpClient()
        result = fr.lookup_unpaywall("", email="me@test.com", http_client=http)
        assert result is None


# ===========================================================================
# Tests: extract_text_from_html
# ===========================================================================


class TestExtractTextFromHtml:
    def test_article_tag_priority(self):
        text = fr.extract_text_from_html(_ARTICLE_HTML)
        assert len(text) >= 200
        assert "B" in text

    def test_strips_noise_tags(self):
        text = fr.extract_text_from_html(_ARTICLE_HTML_WITH_NOISE)
        assert "navigation bar" not in text
        assert "var x" not in text
        assert "footer text" not in text
        assert "C" in text

    def test_body_fallback(self):
        text = fr.extract_text_from_html(_BODY_ONLY_HTML)
        assert "D" in text

    def test_empty_html(self):
        assert fr.extract_text_from_html("") == ""
        assert fr.extract_text_from_html("   ") == ""


# ===========================================================================
# Tests: HTML / embedded table extraction (OT-LIT-POOL-S1)
# ===========================================================================

_ENTITY_PATH_TABLE = """
<table>
  <tr><th>Path Info</th><th>Resolved</th><th>Rate</th></tr>
  <tr><td>Without Entity Path</td><td>102</td><td>34.00%</td></tr>
  <tr><td>With Entity Path</td><td>108</td><td>36.00%</td></tr>
</table>
"""


class TestExtractHtmlTables:
    def test_full_page_and_embedded_tables(self):
        page = f"<html><body><h1>Results</h1>{_ENTITY_PATH_TABLE}</body></html>"
        tables = fr.extract_tables_from_html(page)
        assert len(tables) == 1
        rows = tables[0]["rows"]
        assert rows[1][0] == "Without Entity Path"
        assert rows[1][1] == "102"
        assert rows[1][2] == "34.00%"
        assert rows[2][0] == "With Entity Path"
        assert rows[2][1] == "108"
        assert rows[2][2] == "36.00%"

        embedded = "Intro mentions Table 1 but keeps prose.\n" + _ENTITY_PATH_TABLE + "\nTrailing 102 108 alone is not a table."
        embedded_tables = fr.extract_embedded_html_tables(embedded)
        assert len(embedded_tables) == 1
        assert embedded_tables[0]["rows"][1][1] == "102"
        assert fr.table_reference_count(embedded) >= 1

    def test_table_reference_without_markup_is_not_a_table(self):
        text = "As shown in Table 1, the method improves recall."
        assert fr.extract_embedded_html_tables(text) == []
        assert fr.table_reference_count(text) == 1


# ===========================================================================
# Tests: read_non_arxiv_fulltext — degradation chain
# ===========================================================================


class TestReadNonArxivFulltext:
    def test_unpaywall_html_success(self):
        http = FakeHttpClient()
        up_data = _unpaywall_ok(html_url="https://oa.example/article.html")
        http.route(
            "https://api.unpaywall.org/v2/",
            FakeHttpResponse(status_code=200, text=json.dumps(up_data)),
        )
        http.route(
            "https://oa.example/article.html",
            FakeHttpResponse(
                status_code=200,
                text=_ARTICLE_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            ),
        )
        pdf = FakePdfParser()
        result = fr.read_non_arxiv_fulltext(
            _candidate(doi="10.1234/test"),
            http_client=http,
            pdf_parser=pdf,
            unpaywall_email="me@test.com",
        )
        assert result is not None
        assert result.reading_mode == "html_fulltext"
        assert result.parser_used == "beautifulsoup4"
        assert len(result.content_hash) == 64

    def test_unpaywall_html_fails_falls_to_pdf(self):
        http = FakeHttpClient()
        up_data = _unpaywall_ok(
            html_url="https://oa.example/broken",
            pdf_url="https://oa.example/paper.pdf",
        )
        http.route(
            "https://api.unpaywall.org/v2/",
            FakeHttpResponse(status_code=200, text=json.dumps(up_data)),
        )
        # HTML returns non-200
        http.route(
            "https://oa.example/broken",
            FakeHttpResponse(status_code=500),
        )
        # PDF succeeds
        http.route(
            "https://oa.example/paper.pdf",
            FakeHttpResponse(
                status_code=200,
                content=_PDF_MAGIC + b"rest of pdf",
                headers={"content-type": "application/pdf"},
            ),
        )
        pdf = FakePdfParser(text=_LONG_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(doi="10.1234/test"),
            http_client=http,
            pdf_parser=pdf,
            unpaywall_email="me@test.com",
        )
        assert result is not None
        assert result.reading_mode == "pdf_fulltext"
        assert result.parser_used == "pymupdf"

    def test_direct_pdf_url_fallback(self):
        http = FakeHttpClient()
        # No unpaywall email → skip unpaywall
        http.route(
            "https://publisher.com/paper.pdf",
            FakeHttpResponse(
                status_code=200,
                content=_PDF_MAGIC + b"data",
                headers={"content-type": "application/pdf"},
            ),
        )
        pdf = FakePdfParser(text=_LONG_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(pdf_url="https://publisher.com/paper.pdf"),
            http_client=http,
            pdf_parser=pdf,
        )
        assert result is not None
        assert result.reading_mode == "pdf_fulltext"
        assert result.source_url == "https://publisher.com/paper.pdf"

    def test_landing_page_html_fallback(self):
        http = FakeHttpClient()
        http.route(
            "https://publisher.com/landing",
            FakeHttpResponse(
                status_code=200,
                text=_ARTICLE_HTML,
                headers={"content-type": "text/html"},
            ),
        )
        pdf = FakePdfParser()
        result = fr.read_non_arxiv_fulltext(
            _candidate(landing_page_url="https://publisher.com/landing"),
            http_client=http,
            pdf_parser=pdf,
        )
        assert result is not None
        assert result.reading_mode == "html_fulltext"

    def test_all_strategies_fail_returns_none(self):
        http = FakeHttpClient()  # all 404
        pdf = FakePdfParser()
        result = fr.read_non_arxiv_fulltext(
            _candidate(doi="10.1234/test"),
            http_client=http,
            pdf_parser=pdf,
            unpaywall_email="me@test.com",
        )
        assert result is None

    def test_text_too_short_fails(self):
        http = FakeHttpClient()
        up_data = _unpaywall_ok(html_url="https://oa.example/tiny.html")
        http.route(
            "https://api.unpaywall.org/v2/",
            FakeHttpResponse(status_code=200, text=json.dumps(up_data)),
        )
        http.route(
            "https://oa.example/tiny.html",
            FakeHttpResponse(
                status_code=200,
                text=_TINY_HTML,
                headers={"content-type": "text/html"},
            ),
        )
        pdf = FakePdfParser(text=_SHORT_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(doi="10.1234/test"),
            http_client=http,
            pdf_parser=pdf,
            unpaywall_email="me@test.com",
        )
        assert result is None

    def test_no_email_skips_unpaywall(self):
        http = FakeHttpClient()
        http.route(
            "https://publisher.com/paper.pdf",
            FakeHttpResponse(
                status_code=200,
                content=_PDF_MAGIC + b"data",
                headers={"content-type": "application/pdf"},
            ),
        )
        pdf = FakePdfParser(text=_LONG_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(doi="10.1234/test", pdf_url="https://publisher.com/paper.pdf"),
            http_client=http,
            pdf_parser=pdf,
            unpaywall_email=None,
        )
        assert result is not None
        # Unpaywall API was never called
        assert not any("unpaywall" in url for url, _ in http.calls)

    def test_invalid_scheme_is_rejected(self):
        http = FakeHttpClient()
        pdf = FakePdfParser(text=_LONG_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(pdf_url="file:///tmp/paper.pdf"),
            http_client=http,
            pdf_parser=pdf,
        )
        assert result is None
        assert http.calls == []

    def test_localhost_and_private_ip_are_rejected(self):
        http = FakeHttpClient()
        pdf = FakePdfParser(text=_LONG_TEXT)
        for url in (
            "http://localhost/paper.pdf",
            "http://127.0.0.1/paper.pdf",
            "http://10.0.0.1/paper.pdf",
            "http://192.168.1.10/paper.pdf",
            "http://172.16.0.5/paper.pdf",
        ):
            result = fr.read_non_arxiv_fulltext(
                _candidate(pdf_url=url),
                http_client=http,
                pdf_parser=pdf,
            )
            assert result is None
        assert http.calls == []

    def test_content_length_over_limit_is_rejected(self):
        http = FakeHttpClient()
        http.route(
            "https://publisher.com/huge.pdf",
            FakeHttpResponse(
                status_code=200,
                content=_PDF_MAGIC + b"data",
                headers={
                    "content-type": "application/pdf",
                    "content-length": str(fr._MAX_PDF_BYTES + 1),
                },
            ),
        )
        pdf = FakePdfParser(text=_LONG_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(pdf_url="https://publisher.com/huge.pdf"),
            http_client=http,
            pdf_parser=pdf,
        )
        assert result is None
        assert pdf.calls == []

    def test_streaming_over_limit_is_rejected(self):
        http = FakeHttpClient()
        http.route(
            "https://publisher.com/huge.pdf",
            FakeHttpResponse(
                status_code=200,
                content=_PDF_MAGIC + (b"x" * (fr._MAX_PDF_BYTES + 1)),
                headers={"content-type": "application/pdf"},
            ),
        )
        pdf = FakePdfParser(text=_LONG_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(pdf_url="https://publisher.com/huge.pdf"),
            http_client=http,
            pdf_parser=pdf,
        )
        assert result is None
        assert pdf.calls == []

    def test_pdf_page_cap_is_passed_to_parser(self):
        http = FakeHttpClient()
        http.route(
            "https://publisher.com/paper.pdf",
            FakeHttpResponse(
                status_code=200,
                content=_PDF_MAGIC + b"data",
                headers={"content-type": "application/pdf"},
            ),
        )
        pdf = FakePdfParser(text=_LONG_TEXT)
        result = fr.read_non_arxiv_fulltext(
            _candidate(pdf_url="https://publisher.com/paper.pdf"),
            http_client=http,
            pdf_parser=pdf,
        )
        assert result is not None
        assert pdf.calls == [{"bytes": len(_PDF_MAGIC + b"data"), "max_pages": 20}]

    def test_user_agent_is_sent(self):
        http = FakeHttpClient()
        http.route(
            "https://publisher.com/landing",
            FakeHttpResponse(
                status_code=200,
                text=_ARTICLE_HTML,
                headers={"content-type": "text/html"},
            ),
        )
        result = fr.read_non_arxiv_fulltext(
            _candidate(landing_page_url="https://publisher.com/landing"),
            http_client=http,
            pdf_parser=FakePdfParser(),
        )
        assert result is not None
        assert "academic OA metadata/fulltext validation" in http.calls[0][1]["headers"]["User-Agent"]


# ===========================================================================
# Tests: FulltextReader facade
# ===========================================================================


class TestFulltextReader:
    def test_read_delegates_to_chain(self):
        http = FakeHttpClient()
        http.route(
            "https://publisher.com/paper.pdf",
            FakeHttpResponse(
                status_code=200,
                content=_PDF_MAGIC + b"data",
                headers={"content-type": "application/pdf"},
            ),
        )
        pdf = FakePdfParser(text=_LONG_TEXT)
        reader = fr.FulltextReader(
            http_client=http, pdf_parser=pdf, unpaywall_email=None
        )
        result = reader.read(_candidate(pdf_url="https://publisher.com/paper.pdf"))
        assert result is not None
        assert result.reading_mode == "pdf_fulltext"


# ===========================================================================
# Tests: helpers
# ===========================================================================


class TestHelpers:
    def test_normalize_whitespace(self):
        assert fr._normalize_whitespace("  a   b  ") == "a b"
        assert fr._normalize_whitespace("a\n\n\n\nb") == "a\n\nb"

    def test_sha256_text(self):
        h = fr._sha256_text("hello")
        assert len(h) == 64
        assert h == fr._sha256_text("hello")  # deterministic

    def test_looks_like_pdf_url(self):
        assert fr._looks_like_pdf_url("https://example.com/paper.pdf") is True
        assert fr._looks_like_pdf_url("https://example.com/pdf/12345") is True
        assert fr._looks_like_pdf_url("https://example.com/article") is False

    def test_looks_like_pdf_bytes(self):
        assert fr._looks_like_pdf_bytes(b"%PDF-1.4 data") is True
        assert fr._looks_like_pdf_bytes(b"<html>") is False
        assert fr._looks_like_pdf_bytes(b"") is False

    def test_candidate_doi(self):
        assert fr._candidate_doi({"doi": "10.1234/test"}) == "10.1234/test"
        assert fr._candidate_doi({"external_ids": {"doi": "10.5/x"}}) == "10.5/x"
        assert fr._candidate_doi({"candidate_id": "x"}) == ""


def test_network_timeout_and_retry_are_executed_by_fulltext_path():
    class FlakyHttp:
        def __init__(self):
            self.calls = []

        def get(self, url, *, timeout=30.0, follow_redirects=True, headers=None, max_bytes=None):
            self.calls.append({"url": url, "timeout": timeout, "max_bytes": max_bytes})
            if len(self.calls) == 1:
                raise TimeoutError("transient timeout")
            return FakeHttpResponse(
                status_code=200,
                text=_ARTICLE_HTML,
                content=_ARTICLE_HTML.encode(),
                headers={"content-type": "text/html"},
                url=url,
            )

    http = FlakyHttp()
    result = fr.read_non_arxiv_fulltext(
        _candidate(landing_page_url="https://publisher.com/article"),
        http_client=http,
        pdf_parser=FakePdfParser(),
        timeout=7.5,
        max_retries=1,
    )
    assert result is not None
    assert result.material_type == "article_fulltext"
    assert len(http.calls) == 2
    assert {call["timeout"] for call in http.calls} == {7.5}

"""Shared progressive content-form access for paper identity work.

Responsibility:
- given a discovery hit / candidate, choose credible content forms
- try lowest-cost forms first (metadata → landing/HTML → PDF)
- record each route failure as a local access fact
- never promote a single route failure into "paper unavailable"

This module is intentionally stdlib-only so CS and OT adapters can reuse it
without depending on DeepXiv SDK or PDF parsers at import time.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_USER_AGENT = "OpenAIR-shared-content-form-access/1.0"
_HTTP_TIMEOUT_SECONDS = 30


def _load_access_readiness():
    import sys

    module_name = "shared_source_access_readiness_resolver"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    resolver_path = (
        Path(__file__).resolve().parents[2]
        / "source-access-readiness-resolver-skill"
        / "resolver.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, str(resolver_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load access readiness resolver: {resolver_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def candidate_from_arxiv_hit(hit: Any, *, query: str = "") -> dict[str, Any]:
    """Normalize an ArxivHit into the candidate shape access-readiness expects."""
    arxiv_id = getattr(hit, "arxiv_id", None)
    work_id = arxiv_id.split("v")[0] if isinstance(arxiv_id, str) and arxiv_id else None
    abstract = getattr(hit, "abstract", None)
    comment = getattr(hit, "comment", None)
    return {
        "candidate_id": f"arxiv:{work_id or arxiv_id or 'unknown'}",
        "title": getattr(hit, "title", None),
        "abstract": abstract,
        "comment": comment,
        "doi": getattr(hit, "doi", None),
        "pdf_url": getattr(hit, "pdf_url", None),
        "landing_page_url": getattr(hit, "abs_url", None),
        "source_url": getattr(hit, "abs_url", None) or getattr(hit, "pdf_url", None),
        "external_ids": {"arxiv_id": work_id or arxiv_id},
        "arxiv_id": arxiv_id,
        "matched_query": query,
        "raw_metadata": {
            "arxiv_id": arxiv_id,
            "abs_url": getattr(hit, "abs_url", None),
            "comment": comment,
        },
    }


def plan_content_forms(candidate: dict[str, Any]) -> list[str]:
    """Return preferred content-form order using formal access readiness.

    Product order (lowest cost that remains credible):
      metadata_only → structured (xml/html/tei) → landing_page → pdf

    Access readiness contributes which routes exist; this planner still refuses
    to let PDF outrank HTML/landing when those routes are available.
    """
    resolver = _load_access_readiness()
    (
        _access_status,
        available_types,
        _preferred_type,
        _access_routes,
        _needs_acquisition,
        _acquisition_hint,
        _evidence_fields,
    ) = resolver.resolve_access_status(
        candidate,
        {"prefer_structured_fulltext": True},
    )

    available = set(available_types)
    if candidate.get("landing_page_url") or candidate.get("source_url"):
        available.add("landing_page")
    if candidate.get("pdf_url") or (candidate.get("external_ids") or {}).get("arxiv_id"):
        available.add("pdf")

    ordered: list[str] = []
    if candidate.get("abstract") or candidate.get("comment"):
        ordered.append("metadata_only")

    for access_type in ("xml", "html", "tei", "landing_page", "pdf"):
        if access_type in available and access_type not in ordered:
            ordered.append(access_type)

    for access_type in available_types:
        if access_type not in ordered:
            ordered.append(access_type)
    return ordered


def _http_get_text(url: str) -> tuple[int, str]:
    request = Request(
        url,
        headers={
            "Accept": "text/html, application/xhtml+xml;q=0.9, text/plain;q=0.8, */*;q=0.1",
            "User-Agent": _USER_AGENT,
        },
    )
    with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        status = int(response.getcode() or 200)
        return status, response.read().decode("utf-8", errors="replace")


def _http_get_bytes(url: str) -> tuple[int, bytes]:
    request = Request(
        url,
        headers={
            "Accept": "application/pdf, application/octet-stream;q=0.9, */*;q=0.1",
            "User-Agent": _USER_AGENT,
        },
    )
    with urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        status = int(response.getcode() or 200)
        return status, response.read()


def _pdf_url_for(candidate: dict[str, Any]) -> str | None:
    pdf_url = candidate.get("pdf_url")
    if isinstance(pdf_url, str) and pdf_url.strip():
        return pdf_url.strip()
    arxiv_id = (candidate.get("external_ids") or {}).get("arxiv_id") or candidate.get("arxiv_id")
    if isinstance(arxiv_id, str) and arxiv_id.strip():
        return f"https://arxiv.org/pdf/{arxiv_id.strip()}"
    return None


def _landing_url_for(candidate: dict[str, Any]) -> str | None:
    for key in ("landing_page_url", "source_url"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip() and "/pdf/" not in value.casefold():
            return value.strip()
    arxiv_id = candidate.get("arxiv_id") or (candidate.get("external_ids") or {}).get("arxiv_id")
    if isinstance(arxiv_id, str) and arxiv_id.strip():
        return f"https://arxiv.org/abs/{arxiv_id.strip()}"
    return None


def _metadata_text(candidate: dict[str, Any]) -> str:
    parts = [
        str(candidate.get("title") or "").strip(),
        str(candidate.get("comment") or "").strip(),
        str(candidate.get("abstract") or "").strip(),
    ]
    return "\n".join(part for part in parts if part)


def collect_readable_content_forms(
    candidate: dict[str, Any],
    *,
    allow_pdf: bool = True,
    pdf_text_extractor: Callable[[bytes], str] | None = None,
    http_get_text: Callable[[str], tuple[int, str]] | None = None,
    http_get_bytes: Callable[[str], tuple[int, bytes]] | None = None,
    should_stop: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Try planned content forms; return successes and local failure facts.

    When ``should_stop`` returns True after a succeeded form, later forms
    (including PDF) are not fetched. This preserves structured/metadata-first
    behavior for callers that already obtained enough evidence.

    Each result row:
      {
        "access_type": "metadata_only"|"landing_page"|"html"|"pdf",
        "outcome": "succeeded"|"failed"|"skipped",
        "source_url": str|None,
        "http_status": int|None,
        "text": str|None,
        "notes": str|None,
      }
    """
    get_text = http_get_text or _http_get_text
    get_bytes = http_get_bytes or _http_get_bytes
    results: list[dict[str, Any]] = []

    for access_type in plan_content_forms(candidate):
        row: dict[str, Any]
        if access_type == "metadata_only":
            text = _metadata_text(candidate)
            if text.strip():
                row = {
                    "access_type": "metadata_only",
                    "outcome": "succeeded",
                    "source_url": _landing_url_for(candidate),
                    "http_status": None,
                    "text": text,
                    "notes": "discovery metadata (title/abstract/comment); no extra fetch",
                }
            else:
                row = {
                    "access_type": "metadata_only",
                    "outcome": "failed",
                    "source_url": None,
                    "http_status": None,
                    "text": None,
                    "notes": "no usable metadata text",
                }
            results.append(row)
            if row["outcome"] == "succeeded" and should_stop and should_stop(row):
                break
            continue

        if access_type in {"landing_page", "html"}:
            url = _landing_url_for(candidate)
            if not url:
                results.append({
                    "access_type": access_type,
                    "outcome": "skipped",
                    "source_url": None,
                    "http_status": None,
                    "text": None,
                    "notes": "no landing/html route",
                })
                continue
            try:
                status, body = get_text(url)
            except HTTPError as error:
                results.append({
                    "access_type": access_type,
                    "outcome": "failed",
                    "source_url": url,
                    "http_status": int(error.code),
                    "text": None,
                    "notes": "landing/html route unavailable",
                })
                continue
            except (URLError, TimeoutError, OSError, UnicodeDecodeError):
                results.append({
                    "access_type": access_type,
                    "outcome": "failed",
                    "source_url": url,
                    "http_status": None,
                    "text": None,
                    "notes": "landing/html route unavailable",
                })
                continue
            row = {
                "access_type": access_type,
                "outcome": "succeeded",
                "source_url": url,
                "http_status": status,
                "text": body,
                "notes": "landing/html content",
            }
            results.append(row)
            if should_stop and should_stop(row):
                break
            continue

        if access_type == "pdf":
            if not allow_pdf:
                results.append({
                    "access_type": "pdf",
                    "outcome": "skipped",
                    "source_url": _pdf_url_for(candidate),
                    "http_status": None,
                    "text": None,
                    "notes": "pdf disabled by caller",
                })
                continue
            url = _pdf_url_for(candidate)
            if not url:
                results.append({
                    "access_type": "pdf",
                    "outcome": "skipped",
                    "source_url": None,
                    "http_status": None,
                    "text": None,
                    "notes": "no pdf route",
                })
                continue
            try:
                status, payload = get_bytes(url)
                if pdf_text_extractor is None:
                    raise RuntimeError("pdf_text_extractor required for pdf route")
                text = pdf_text_extractor(payload)
            except HTTPError as error:
                results.append({
                    "access_type": "pdf",
                    "outcome": "failed",
                    "source_url": url,
                    "http_status": int(error.code),
                    "text": None,
                    "notes": "pdf route unavailable",
                })
                continue
            except (URLError, TimeoutError, OSError, RuntimeError, ValueError, ImportError):
                results.append({
                    "access_type": "pdf",
                    "outcome": "failed",
                    "source_url": url,
                    "http_status": None,
                    "text": None,
                    "notes": "pdf route unavailable",
                })
                continue
            row = {
                "access_type": "pdf",
                "outcome": "succeeded",
                "source_url": url,
                "http_status": status,
                "text": text,
                "notes": "pdf fulltext",
            }
            results.append(row)
            if should_stop and should_stop(row):
                break
            continue

        results.append({
            "access_type": access_type,
            "outcome": "skipped",
            "source_url": None,
            "http_status": None,
            "text": None,
            "notes": f"unsupported access type in this entrypoint: {access_type}",
        })

    return results

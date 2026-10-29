"""Deterministic identity verification and material admission.

Acquisition and admission are deliberately separate.  Provider-observed identity
must come from fetched/provider payloads; requested identity is never copied into
that field.  Only verified article body material can become MATERIAL_READY.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable

_ABSTRACT_NAMES = {"abstract", "summary", "metadata", "title"}
_NON_BODY_MARKERS = {
    "access denied", "sign in", "log in", "login", "captcha", "robot check",
    "privacy policy", "terms of use", "cookie policy", "subscribe to",
    "page not found", "404 not found", "service unavailable", "error occurred",
}
_SUPPLEMENT_MARKERS = {
    "supplementary material", "supplementary information", "supporting information",
    "supplemental material", "supplemental information",
}
_REFERENCE_HEADINGS = {
    "references", "bibliography", "reference list", "works cited", "literature cited",
}
_ARXIV_RE = re.compile(
    r"(?P<work>\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z\-]+)?/\d{7})(?P<version>v\d+)?",
    re.I,
)
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


def _text(value: Any) -> str:
    return "" if value is None else str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _hash(value: str) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def _norm_doi(value: Any) -> str:
    doi = _text(value).casefold()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.strip().rstrip("./")


def normalize_arxiv_id(value: Any) -> dict[str, str]:
    match = _ARXIV_RE.search(_text(value))
    if not match:
        return {"work_id": "", "version": "", "versioned_id": ""}
    work = match.group("work")
    version = (match.group("version") or "").lower()
    return {"work_id": work, "version": version, "versioned_id": f"{work}{version}"}


def _external(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("external_ids")
    return value if isinstance(value, dict) else {}


def _candidate_id(candidate: dict[str, Any], key: str) -> str:
    return _text(candidate.get(key) or _external(candidate).get(key))


def requested_identity(candidate: dict[str, Any]) -> dict[str, str]:
    arxiv = normalize_arxiv_id(_candidate_id(candidate, "arxiv_id"))
    return {
        "candidate_id": _text(candidate.get("candidate_id") or candidate.get("dedupe_key")),
        "doi": _norm_doi(_candidate_id(candidate, "doi")),
        "arxiv_id": arxiv["versioned_id"],
        "arxiv_work_id": arxiv["work_id"],
        "arxiv_version": arxiv["version"],
        "pmcid": _candidate_id(candidate, "pmcid").casefold(),
        "openalex_id": _candidate_id(candidate, "openalex_id").casefold(),
        "title": _text(candidate.get("title")),
    }


def normalize_observed_identity(raw: Any) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    arxiv = normalize_arxiv_id(raw.get("arxiv_id"))
    return {
        "doi": _norm_doi(raw.get("doi")),
        "arxiv_id": arxiv["versioned_id"],
        "arxiv_work_id": arxiv["work_id"],
        "arxiv_version": arxiv["version"],
        "pmcid": _text(raw.get("pmcid")).casefold(),
        "openalex_id": _text(raw.get("openalex_id")).casefold(),
    }


_DOCUMENT_IDENTITY_CONTAINERS = ("metadata", "paper", "article", "document", "identity")


def _document_identity_rows(payload: Any) -> Iterable[dict[str, Any]]:
    """Yield only document-scoped metadata rows, never citations/references."""
    if not isinstance(payload, dict):
        return
    yield payload
    for key in _DOCUMENT_IDENTITY_CONTAINERS:
        nested = payload.get(key)
        if isinstance(nested, dict):
            yield nested


def observed_identity_from_payload(*payloads: Any) -> dict[str, str]:
    """Extract self-identity only from document-scoped provider metadata.

    Arbitrary nested dictionaries are intentionally not traversed: citation and
    reference payloads can contain durable IDs for other papers.
    """
    observed: dict[str, str] = {"doi": "", "arxiv_id": "", "pmcid": "", "openalex_id": ""}
    for payload in payloads:
        for row in _document_identity_rows(payload):
            for key in ("arxiv_id", "arxivId", "arxiv", "paper_id"):
                value = row.get(key)
                if value and normalize_arxiv_id(value)["versioned_id"]:
                    observed["arxiv_id"] = normalize_arxiv_id(value)["versioned_id"]
                    break
            for key in ("doi", "DOI"):
                value = row.get(key)
                if value and _norm_doi(value):
                    observed["doi"] = _norm_doi(value)
                    break
            for key in ("pmcid", "pmc_id", "pmc"):
                value = row.get(key)
                if value:
                    observed["pmcid"] = _text(value).casefold()
                    break
            for key in ("openalex_id", "openalex", "openalexId"):
                value = row.get(key)
                if value:
                    observed["openalex_id"] = _text(value).casefold()
                    break
            for key in ("url", "source_url", "paper_url"):
                value = row.get(key)
                if value and not observed["arxiv_id"]:
                    arxiv = normalize_arxiv_id(value)
                    if arxiv["versioned_id"]:
                        observed["arxiv_id"] = arxiv["versioned_id"]
                if value and not observed["doi"]:
                    match = _DOI_RE.search(_text(value))
                    if match:
                        observed["doi"] = _norm_doi(match.group(0))
    return normalize_observed_identity(observed)


def verify_identity(
    requested: dict[str, str],
    observed_raw: Any,
    *,
    observed_title: str = "",
) -> tuple[dict[str, str] | None, str]:
    """Return resolved identity only when provider-observed durable IDs verify it."""
    observed = normalize_observed_identity(observed_raw)
    # Title text is descriptive metadata, not document identity authority.
    # Provider/publisher titles may legitimately vary while a provider-observed
    # durable identifier still proves the same work. Title alone remains
    # insufficient to authenticate a document.
    del observed_title

    # Durable identity/version conflicts are hard failures.
    # Any conflicting durable identifier is a hard failure, even if another ID matches.
    for key in ("doi", "pmcid", "openalex_id"):
        expected = requested.get(key, "")
        actual = observed.get(key, "")
        if expected and actual and expected != actual:
            return None, f"{key}_conflict"

    expected_work = requested.get("arxiv_work_id", "")
    actual_work = observed.get("arxiv_work_id", "")
    if expected_work and actual_work and expected_work.casefold() != actual_work.casefold():
        return None, "arxiv_work_conflict"
    # A concrete arXiv version is part of the requested identity.  Matching a DOI
    # proves the work, not the exact arXiv version, so the provider must observe it.
    if requested.get("arxiv_version"):
        if not actual_work:
            return None, "arxiv_version_unverified"
        actual_version = observed.get("arxiv_version", "")
        if not actual_version:
            return None, "arxiv_version_unverified"
        if requested["arxiv_version"] != actual_version:
            return None, "arxiv_version_conflict"

    basis = ""
    if requested.get("doi") and observed.get("doi") == requested.get("doi"):
        basis = "doi_match"
    elif requested.get("pmcid") and observed.get("pmcid") == requested.get("pmcid"):
        basis = "pmcid_match"
    elif requested.get("openalex_id") and observed.get("openalex_id") == requested.get("openalex_id"):
        basis = "openalex_id_match"
    elif expected_work and actual_work == expected_work:
        if requested.get("arxiv_version") and observed.get("arxiv_version") != requested.get("arxiv_version"):
            return None, "arxiv_version_unverified"
        basis = "arxiv_match"
    else:
        return None, "identity_unverified"

    resolved = dict(requested)
    # A provider may resolve an unversioned arXiv request to a concrete version.
    if observed.get("arxiv_id"):
        resolved["arxiv_id"] = observed["arxiv_id"]
        resolved["arxiv_work_id"] = observed["arxiv_work_id"]
        resolved["arxiv_version"] = observed["arxiv_version"]
    for key in ("doi", "pmcid", "openalex_id"):
        if observed.get(key):
            resolved[key] = observed[key]
    return resolved, basis


def _is_supplement_name(name: str) -> bool:
    folded = _text(name).casefold()
    return any(marker in folded for marker in _SUPPLEMENT_MARKERS)



def _supplement_in_front_matter(content: str) -> bool:
    lines = [re.sub(r"\s+", " ", line).strip().casefold() for line in _text(content).splitlines()[:16]]
    for line in lines:
        if not line or len(line) > 180:
            continue
        if any(line.startswith(marker) or line == marker for marker in _SUPPLEMENT_MARKERS):
            return True
    return False


def _normalized_heading(value: Any) -> str:
    heading = _text(value).casefold()
    heading = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+)[\s.)-]+", "", heading)
    return re.sub(r"[^a-z]+", " ", heading).strip()


def _is_reference_only(section_name: str, content: str) -> bool:
    """Reject obvious bibliography/reference-only material without penalizing article bodies.

    A normal article may mention or contain references near the end.  We therefore only
    reject a clearly labelled reference section, or a fulltext/raw payload whose front
    matter itself starts with a reference-list heading.
    """
    heading = _normalized_heading(section_name)
    if heading in _REFERENCE_HEADINGS or any(heading.startswith(f"{marker} ") for marker in _REFERENCE_HEADINGS):
        return True

    text = _text(content).lstrip()
    if not text:
        return False

    if "\n" in text:
        first_line = text.splitlines()[0][:160]
        front_heading = _normalized_heading(first_line)
        return front_heading in _REFERENCE_HEADINGS

    # PDF extraction can flatten a bibliography into one line.  Only treat that as
    # reference-only when the heading is immediately followed by a citation marker;
    # a scientific sentence such as "References were ..." remains admissible.
    return bool(re.match(
        r"^\s*(?:references|bibliography|reference\s+list|works\s+cited|literature\s+cited)"
        r"\s*(?:[:.-]?\s*)(?:\[\s*\d+\s*\]|\d+[.)])\s+",
        text,
        re.I,
    ))


def _section_material_type(name: str, content: str) -> str:
    folded_name = _text(name).casefold()
    folded_content = _text(content).casefold().lstrip()
    if (
        _is_supplement_name(folded_name)
        or any(folded_content.startswith(marker) for marker in _SUPPLEMENT_MARKERS)
        or _supplement_in_front_matter(content)
    ):
        return "supplementary_material"
    if folded_name in _ABSTRACT_NAMES:
        return "abstract_only"
    return "article_fulltext"

def _looks_scientific_body(section_name: str, content: str) -> bool:
    name = _text(section_name).casefold()
    text = _text(content)
    folded = text.casefold()
    if (
        not text
        or name in _ABSTRACT_NAMES
        or _is_supplement_name(name)
        or _supplement_in_front_matter(text)
        or _is_reference_only(section_name, text)
    ):
        return False
    if any(marker in folded for marker in _NON_BODY_MARKERS):
        cleaned = folded
        for marker in _NON_BODY_MARKERS:
            cleaned = cleaned.replace(marker, " ")
        if len(re.sub(r"\s+", " ", cleaned).strip()) < 600:
            return False
    return True


def admit_material(candidate: dict[str, Any], acquired: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    requested = requested_identity(candidate)
    observed = normalize_observed_identity(acquired.get("provider_observed_identity"))
    material_type = _text(acquired.get("material_type") or "article_fulltext").casefold()
    if material_type != "article_fulltext":
        return None, {
            "reason": f"material_type_not_admissible:{material_type or 'unknown'}",
            "requested_identity": requested,
            "provider_observed_identity": observed,
            "material_type": material_type or "unknown",
        }

    resolved, basis = verify_identity(
        requested,
        observed,
        observed_title=_text(acquired.get("provider_observed_title")),
    )
    if resolved is None:
        reason = "same_work_identity_conflict" if "conflict" in basis else "same_work_identity_unverified"
        return None, {
            "reason": reason,
            "identity_basis": basis,
            "requested_identity": requested,
            "provider_observed_identity": observed,
            "provider_observed_title": _text(acquired.get("provider_observed_title")),
        }

    locator = _text(acquired.get("source_locator"))
    if not locator:
        return None, {"reason": "stable_locator_missing", "requested_identity": requested, "provider_observed_identity": observed}

    provider = _text(acquired.get("provider"))
    ready: list[dict[str, Any]] = []
    for section in acquired.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_material_type = _text(section.get("material_type") or material_type).casefold()
        name = _text(section.get("section_name")) or "fulltext"
        content = _text(section.get("content"))
        if section_material_type != "article_fulltext" or not _looks_scientific_body(name, content):
            continue
        ready.append({
            "section_name": name,
            "content": content,
            "content_hash": _hash(content),
            "source_type": provider,
            "provider": provider,
            "source_locator": locator,
            "paper_identity": resolved,
            "material_type": "article_fulltext",
            "material_status": "MATERIAL_READY",
        })
    if not ready:
        return None, {
            "reason": "substantive_scientific_text_missing",
            "requested_identity": requested,
            "provider_observed_identity": observed,
            "resolved_identity": resolved,
            "material_type": material_type,
        }
    return {
        "requested_identity": requested,
        "provider_observed_identity": observed,
        "resolved_identity": resolved,
        "paper_identity": resolved,
        "provider": provider,
        "source_locator": locator,
        "sections": ready,
        "material_type": "article_fulltext",
        "material_status": "MATERIAL_READY",
        "identity_basis": basis,
    }, None


def _xml_title_and_sections(xml_text: str, *, jats: bool) -> tuple[str, list[dict[str, str]], dict[str, str]]:
    root = ET.fromstring(xml_text)
    title_node = root.find(".//article-title") if jats else root.find(".//{*}titleStmt/{*}title")
    title = _text(" ".join(title_node.itertext())) if title_node is not None else ""
    observed: dict[str, str] = {}
    if jats:
        for node in root.findall(".//article-id"):
            kind = _text(node.attrib.get("pub-id-type")).casefold()
            value = _text(" ".join(node.itertext()))
            if kind == "doi": observed["doi"] = value
            elif kind in {"pmc", "pmcid"}: observed["pmcid"] = value
    else:
        for node in root.findall(".//{*}idno"):
            kind = _text(node.attrib.get("type")).casefold()
            value = _text(" ".join(node.itertext()))
            if kind == "doi": observed["doi"] = value
            elif "arxiv" in kind: observed["arxiv_id"] = value
    nodes = root.findall(".//sec") if jats else root.findall(".//{*}div")
    sections: list[dict[str, str]] = []
    for index, node in enumerate(nodes):
        head = node.find("title") if jats else node.find("{*}head")
        name = _text(" ".join(head.itertext())) if head is not None else f"section_{index + 1}"
        content = _text(" ".join(node.itertext()))
        if content:
            sections.append({"section_name": name, "content": content, "material_type": _section_material_type(name, content)})
    return title, sections, normalize_observed_identity(observed)


def _decode_tei(raw: Any) -> tuple[str | None, str | None]:
    if isinstance(raw, str) and raw.lstrip().startswith("<"):
        return raw, None
    if isinstance(raw, str):
        raw = raw.encode("latin-1", errors="ignore")
    if not isinstance(raw, (bytes, bytearray)):
        return None, "content_fetch_unavailable"
    payload = bytes(raw)
    if payload[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(payload).decode("utf-8"), None
        except (OSError, UnicodeDecodeError):
            return None, "content_integrity_gzip_invalid"
    try:
        return payload.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "content_integrity_invalid_tei"


def acquire_embedded_material(candidate: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Normalize provider payloads already carried by the selected candidate."""
    provider = _text(candidate.get("content_provider") or candidate.get("provider")).casefold()
    locator = _text(candidate.get("source_locator") or candidate.get("content_url"))
    if provider == "openalex":
        xml, reason = _decode_tei(candidate.get("grobid_xml"))
        if reason:
            return None, {"reason": reason}
        try:
            title, sections, observed = _xml_title_and_sections(xml or "", jats=False)
        except ET.ParseError:
            return None, {"reason": "content_integrity_invalid_tei"}
        if sum(len(_text(section.get("content"))) for section in sections) < 200:
            return None, {"reason": "content_integrity_tei_body_insufficient"}
        material_types = {section["material_type"] for section in sections}
        material_type = next(iter(material_types)) if len(material_types) == 1 else "article_fulltext"
        return {"provider": "openalex_grobid", "source_locator": locator, "provider_observed_title": title,
                "provider_observed_identity": observed, "material_type": material_type, "sections": sections}, None
    if provider in {"europe_pmc", "europepmc"}:
        raw = candidate.get("xml") or candidate.get("europe_pmc_xml")
        if not isinstance(raw, str):
            return None, {"reason": "content_fetch_unavailable"}
        try:
            title, sections, observed = _xml_title_and_sections(raw, jats=True)
        except ET.ParseError:
            return None, {"reason": "content_integrity_invalid_xml"}
        material_types = {section["material_type"] for section in sections}
        material_type = next(iter(material_types)) if len(material_types) == 1 else "article_fulltext"
        return {"provider": "europe_pmc_xml", "source_locator": locator, "provider_observed_title": title,
                "provider_observed_identity": observed, "material_type": material_type, "sections": sections}, None
    if provider == "core":
        fulltext = candidate.get("fullText")
        if not isinstance(fulltext, str) or not _text(fulltext):
            has_url = any(candidate.get(k) for k in ("downloadUrl", "fullTextUrl", "sourceFulltextUrls", "sourceFullTextUrls"))
            return None, {"reason": "core_download_url_only_no_inline_fulltext" if has_url else "core_inline_fulltext_missing"}
        # CORE inline body text cannot authenticate itself: references may contain
        # the requested DOI.  Until provider-scoped identity metadata is present in
        # the formal input contract, this material remains identity-unverified.
        observed = normalize_observed_identity({})
        material_type = _section_material_type("fulltext", fulltext)
        section_name = "Supplementary Material" if material_type == "supplementary_material" else "fulltext"
        return {"provider": "core", "source_locator": locator, "provider_observed_title": _text(candidate.get("provider_title")),
                "provider_observed_identity": observed, "material_type": material_type,
                "sections": [{"section_name": section_name, "content": fulltext, "material_type": material_type}]}, None
    return None, {"reason": "content_fetch_unavailable"}

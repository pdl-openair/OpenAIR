"""Canonical Top-K -> acquired material -> verified PaperPacket pipeline."""
import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SECTION_PRIORITY = [
    "method", "methods", "experimental", "experiment", "experiments",
    "results and discussion", "result", "results", "discussion",
    "mechanism", "conclusion", "conclusions",
]
_ARXIV_ID_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5}(?:v\d+)?)(?!\d)", re.I)
_LEGACY_ARXIV_ID_RE = re.compile(r"([a-z\-]+(?:\.[a-z\-]+)?/\d{7}(?:v\d+)?)", re.I)
_SUPPLEMENT_MARKERS = (
    "supplementary information", "supplementary material",
    "supporting information", "supplemental information", "supplemental material",
)


@dataclass(frozen=True)
class ReadingConfig:
    max_papers: int = 12
    max_sections_per_paper: int = 3
    allow_raw_fallback: bool = False
    section_priority: tuple[str, ...] = tuple(DEFAULT_SECTION_PRIORITY)
    enable_non_arxiv_fulltext: bool = False
    allow_pdf_fulltext: bool = True
    network_timeout_seconds: float = 20.0
    max_provider_retries: int = 1
    max_total_chars: int = 300_000


@dataclass
class AcquisitionResult:
    provider: str
    source_locator: str
    material_type: str
    provider_observed_identity: dict[str, Any] | None
    provider_observed_title: str = ""
    sections: list[dict[str, Any]] = field(default_factory=list)
    brief: Any = field(default_factory=dict)
    head: Any = field(default_factory=dict)
    reading_mode: str = ""
    tables: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _text(value: Any) -> str:
    return "" if value is None else str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-")
    return slug[:80] or "section"


def _load_local_module(module_name: str, filename: str) -> Any:
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _admission() -> Any:
    return _load_local_module("opentrajectory_material_admission", "material_admission.py")


def _fulltext_module() -> Any:
    return _load_local_module("deepxiv_progressive_reading_fulltext_reader", "fulltext_reader.py")


def _guidance() -> Any:
    return _load_local_module("deepxiv_progressive_reading_evidence_guidance", "evidence_guidance.py")


def _extract_arxiv_id_from_text(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = _ARXIV_ID_RE.search(text) or _LEGACY_ARXIV_ID_RE.search(text)
    return match.group(1) if match else None


def _walk_values(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield nested
            yield from _walk_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield nested
            yield from _walk_values(nested)


def extract_arxiv_id(candidate: dict[str, Any]) -> str | None:
    direct = [
        candidate.get("arxiv_id"),
        (candidate.get("external_ids") or {}).get("arxiv_id") if isinstance(candidate.get("external_ids"), dict) else None,
        candidate.get("dedupe_key"), candidate.get("source_url"),
        candidate.get("landing_page_url"), candidate.get("url"),
    ]
    for value in direct:
        arxiv_id = _extract_arxiv_id_from_text(value)
        if arxiv_id:
            return arxiv_id
    for value in _walk_values(candidate.get("provenance") or []):
        arxiv_id = _extract_arxiv_id_from_text(value)
        if arxiv_id:
            return arxiv_id
    return None


def _parse_config(input_data: dict[str, Any]) -> ReadingConfig:
    raw = input_data.get("reading_config") or {}
    if not isinstance(raw, dict):
        raise ValueError("reading_config must be an object")
    priority = raw.get("section_priority")
    if priority is None:
        priority = DEFAULT_SECTION_PRIORITY
    if not isinstance(priority, list) or not priority or not all(isinstance(item, str) and item.strip() for item in priority):
        raise ValueError("reading_config.section_priority must be a non-empty string array")
    return ReadingConfig(
        max_papers=max(1, min(12, int(raw.get("max_papers", 12)))),
        max_sections_per_paper=max(1, min(5, int(raw.get("max_sections_per_paper", 3)))),
        allow_raw_fallback=bool(raw.get("allow_raw_fallback", False)),
        section_priority=tuple(_text(item) for item in priority),
        enable_non_arxiv_fulltext=bool(raw.get("enable_non_arxiv_fulltext", False)),
        allow_pdf_fulltext=bool(raw.get("allow_pdf_fulltext", True)),
        network_timeout_seconds=max(1.0, min(60.0, float(raw.get("network_timeout_seconds", 20.0)))),
        max_provider_retries=max(0, min(2, int(raw.get("max_provider_retries", 1)))),
        max_total_chars=max(10_000, min(2_000_000, int(raw.get("max_total_chars", 300_000)))),
    )


def _selected_candidates(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    topk = input_data.get("topk_selection")
    if not isinstance(topk, dict):
        raise ValueError("canonical input requires topk_selection object")
    selected = topk.get("selected_for_p03")
    if not isinstance(selected, list):
        raise ValueError("topk_selection.selected_for_p03 must be an array")

    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(selected):
        if not isinstance(row, dict):
            raise ValueError(f"topk_selection.selected_for_p03[{index}] must be an object")
        if not _text(row.get("candidate_id")):
            raise ValueError(f"topk_selection.selected_for_p03[{index}].candidate_id is required")
        candidates.append(dict(row))
    return candidates


def _evidence_schema(input_data: dict[str, Any]) -> dict[str, Any]:
    return _guidance().validate_evidence_schema(input_data)


def _create_reader(*, timeout: float, max_retries: int) -> Any:
    try:
        from deepxiv_sdk import Reader
    except ImportError as exc:  # pragma: no cover - live runtime only
        raise RuntimeError("deepxiv-sdk is required for deepxiv_progressive_reading") from exc
    token = os.environ.get("OT_DEEPXIV_TOKEN")
    kwargs = {"timeout": timeout, "max_retries": max_retries}
    if token:
        kwargs["token"] = token
    return Reader(**kwargs)


def _create_fulltext_reader(config: ReadingConfig) -> Any:
    mod = _fulltext_module()
    return mod.FulltextReader(
        http_client=mod.HttpxClient(),
        pdf_parser=mod.PyMuPDFParser(),
        unpaywall_email=os.environ.get("UNPAYWALL_EMAIL") or None,
        allow_pdf_fulltext=config.allow_pdf_fulltext,
        timeout=config.network_timeout_seconds,
        max_retries=config.max_provider_retries,
    )


def _section_names(head: Any) -> list[str]:
    if not isinstance(head, dict):
        return []
    raw = head.get("sections")
    names: list[str] = []
    if isinstance(raw, dict):
        names.extend(_text(name) for name in raw)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                names.append(_text(item))
            elif isinstance(item, dict):
                names.append(_text(item.get("name") or item.get("title") or item.get("section") or item.get("section_name")))
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _is_supplement_name(value: str) -> bool:
    folded = _text(value).casefold()
    return any(marker in folded for marker in _SUPPLEMENT_MARKERS)


def _section_families(value: str) -> set[str]:
    return _guidance().section_families(value)


def _target_section_order(head: Any, priority: tuple[str, ...], targets: list[dict[str, Any]]) -> list[str]:
    available = [name for name in _section_names(head) if not _is_supplement_name(name)]
    default_order = _choose_sections(head, priority, len(available))
    return _guidance().section_order(available, default_order, targets)


def _choose_sections(head: Any, priority: tuple[str, ...], limit: int) -> list[str]:
    """Choose a small but scientifically diverse set of provider sections.

    Priority remains caller-controlled, but aliases such as Methods/Experimental no
    longer consume the entire section budget before Results or Discussion can be read.
    """
    available = [name for name in _section_names(head) if not _is_supplement_name(name)]
    chosen: list[str] = []
    seen: set[str] = set()
    covered_families: set[str] = set()

    for term in (item.casefold() for item in priority):
        for name in available:
            key = name.casefold()
            if key in seen or not (term == key or term in key):
                continue
            families = _section_families(name)
            if families and families.issubset(covered_families):
                continue
            chosen.append(name)
            seen.add(key)
            covered_families.update(families)
            break
        if len(chosen) >= limit:
            return chosen

    # Prefer additional section families before spending remaining budget on another
    # section from a family already represented above.
    for name in available:
        key = name.casefold()
        families = _section_families(name)
        if key in seen or (families and families.issubset(covered_families)):
            continue
        chosen.append(name)
        seen.add(key)
        covered_families.update(families)
        if len(chosen) >= limit:
            return chosen

    for name in available:
        key = name.casefold()
        if key not in seen:
            chosen.append(name)
            seen.add(key)
            if len(chosen) >= limit:
                break
    return chosen


def _extract_content(payload: Any) -> str:
    if isinstance(payload, str):
        return _text(payload)
    if isinstance(payload, dict):
        for key in ("content", "markdown", "text", "section_content", "raw"):
            if isinstance(payload.get(key), str) and payload[key].strip():
                return _text(payload[key])
    return ""


def _provider_section_name(requested_name: str, payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("section_name", "name", "title", "section"):
            if _text(payload.get(key)):
                return _text(payload[key])
    return requested_name


def _material_type(section_name: str, payload: Any, content: str) -> str:
    if isinstance(payload, dict):
        explicit = _text(payload.get("material_type") or payload.get("document_type")).casefold()
        mapping = {
            "article_fulltext": "article_fulltext", "fulltext": "article_fulltext", "article": "article_fulltext",
            "abstract": "abstract_only", "abstract_only": "abstract_only",
            "supplement": "supplementary_material", "supplementary": "supplementary_material",
            "supplementary_material": "supplementary_material", "supporting_information": "supplementary_material",
        }
        if explicit in mapping:
            return mapping[explicit]
    if _is_supplement_name(section_name) or any(_text(content).casefold().lstrip().startswith(marker) for marker in _SUPPLEMENT_MARKERS):
        return "supplementary_material"
    if section_name.casefold() in {"abstract", "summary"}:
        return "abstract_only"
    return "article_fulltext"


def _provider_title(*payloads: Any) -> str:
    for payload in payloads:
        if isinstance(payload, dict) and _text(payload.get("title")):
            return _text(payload["title"])
    return ""


def _acquire_deepxiv(candidate: dict[str, Any], reader: Any, config: ReadingConfig, evidence_schema: dict[str, Any]) -> tuple[AcquisitionResult | None, dict[str, Any] | None, int]:
    arxiv_id = extract_arxiv_id(candidate)
    if not arxiv_id:
        return None, {"reason": "missing_arxiv_id", "provider_attempted": "deepxiv"}, 0
    try:
        brief = reader.brief(arxiv_id)
        head = reader.head(arxiv_id)
    except Exception as exc:
        return None, {"reason": "deepxiv_brief_or_head_failed", "provider_attempted": "deepxiv", "error_type": type(exc).__name__, "error": str(exc)}, 0

    try:
        observed = _merge_observed(
            _admission().observed_identity_from_payload(brief),
            _admission().observed_identity_from_payload(head),
        )
    except ProviderIdentityConflict as exc:
        return None, {
            "reason": "provider_identity_inconsistent",
            "provider_attempted": "deepxiv",
            "identity_basis": str(exc),
        }, 0
    sections: list[dict[str, Any]] = []
    section_errors: list[dict[str, Any]] = []
    raw_calls = 0
    targets = list(evidence_schema["evidence_targets"])
    goals = _guidance().goal_targets(targets)
    section_order = _target_section_order(head, config.section_priority, targets)
    for requested_name in section_order:
        if len(sections) >= config.max_sections_per_paper:
            break
        try:
            payload = reader.section(arxiv_id, requested_name)
            content = _extract_content(payload)
            if not content:
                section_errors.append({"section_name": requested_name, "reason": "empty_section_content"})
                continue
            try:
                observed = _merge_observed(observed, _admission().observed_identity_from_payload(payload))
            except ProviderIdentityConflict as exc:
                return None, {
                    "reason": "provider_identity_inconsistent",
                    "provider_attempted": "deepxiv",
                    "identity_basis": str(exc),
                    "section_name": requested_name,
                }, raw_calls
            provider_name = _provider_section_name(requested_name, payload)
            sections.append({
                "section_name": provider_name,
                "content": content,
                "material_type": _material_type(provider_name, payload, content),
            })
            if _guidance().goals_located(goals, sections):
                break
        except Exception as exc:
            section_errors.append({"section_name": requested_name, "reason": "section_read_failed", "error_type": type(exc).__name__, "error": str(exc)})

    if not sections and config.allow_raw_fallback:
        try:
            raw_calls = 1
            raw_payload = reader.raw(arxiv_id)
            content = _extract_content(raw_payload)
            if content:
                try:
                    observed = _merge_observed(observed, _admission().observed_identity_from_payload(raw_payload))
                except ProviderIdentityConflict as exc:
                    return None, {
                        "reason": "provider_identity_inconsistent",
                        "provider_attempted": "deepxiv",
                        "identity_basis": str(exc),
                        "section_name": "__raw_fallback__",
                    }, raw_calls
                sections.append({"section_name": "__raw_fallback__", "content": content,
                                 "material_type": _material_type("__raw_fallback__", raw_payload, content)})
        except Exception as exc:
            section_errors.append({"section_name": "__raw_fallback__", "reason": "raw_fallback_failed", "error_type": type(exc).__name__, "error": str(exc)})

    if not sections:
        return None, {"reason": "no_readable_sections", "provider_attempted": "deepxiv", "diagnostics": {"section_errors": section_errors, "available_section_names": _section_names(head)}}, raw_calls

    observed_arxiv = _text((observed or {}).get("arxiv_id"))
    section_types = {str(section.get("material_type") or "") for section in sections}
    acquisition_material_type = next(iter(section_types)) if len(section_types) == 1 else "article_fulltext"
    return AcquisitionResult(
        provider="deepxiv",
        source_locator=f"deepxiv://arxiv/{observed_arxiv}" if observed_arxiv else "",
        material_type=acquisition_material_type,
        provider_observed_identity=observed,
        provider_observed_title=_provider_title(brief, head),
        sections=sections,
        brief=brief,
        head=head,
        reading_mode="raw_fallback" if sections[0]["section_name"] == "__raw_fallback__" else "section",
        diagnostics={"selected_section_names": [s["section_name"] for s in sections], "evidence_guided_section_order": section_order, "target_goal_ids": [str(target.get("target_id")) for target in goals], "section_errors": section_errors, "raw_fallback_used": bool(raw_calls)},
    ), None, raw_calls


class ProviderIdentityConflict(ValueError):
    pass


def _merge_observed(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    left = _admission().normalize_observed_identity(a)
    right = _admission().normalize_observed_identity(b)
    for key in ("doi", "pmcid", "openalex_id"):
        if left.get(key) and right.get(key) and left[key] != right[key]:
            raise ProviderIdentityConflict(f"{key}_conflict")

    left_work = left.get("arxiv_work_id", "")
    right_work = right.get("arxiv_work_id", "")
    if left_work and right_work and left_work.casefold() != right_work.casefold():
        raise ProviderIdentityConflict("arxiv_work_conflict")
    left_version = left.get("arxiv_version", "")
    right_version = right.get("arxiv_version", "")
    if left_version and right_version and left_version != right_version:
        raise ProviderIdentityConflict("arxiv_version_conflict")

    merged = dict(left)
    for key, value in right.items():
        if value and not merged.get(key):
            merged[key] = value
    # Prefer the more specific versioned observation when the work is the same.
    if right.get("arxiv_id") and right_version and not left_version:
        merged["arxiv_id"] = right["arxiv_id"]
        merged["arxiv_work_id"] = right_work
        merged["arxiv_version"] = right_version
    return merged if any(merged.values()) else None


def _acquire_fulltext(candidate: dict[str, Any], fulltext_reader: Any) -> tuple[AcquisitionResult | None, dict[str, Any] | None]:
    result = fulltext_reader.read(candidate)
    if result is None:
        steps = getattr(fulltext_reader, "last_diagnostics", None)
        return None, {"reason": "all_fulltext_strategies_failed", "provider_attempted": "fulltext", "diagnostics": {"steps": list(steps or [])}}
    provider = _text(getattr(result, "acquisition_provider", "") or getattr(result, "reading_mode", "") or "fulltext")
    material_type = _text(getattr(result, "material_type", "") or "article_fulltext")
    section_name = {
        "supplementary_material": "Supplementary Material",
        "abstract_only": "Abstract",
        "landing_page": "Landing Page",
    }.get(material_type, "fulltext")
    return AcquisitionResult(
        provider=provider,
        source_locator=_text(getattr(result, "source_url", "")),
        material_type=material_type,
        provider_observed_identity=getattr(result, "provider_observed_identity", None),
        provider_observed_title=_text(getattr(result, "provider_observed_title", "")),
        sections=[{"section_name": section_name, "content": _text(getattr(result, "text", "")), "material_type": material_type}],
        brief={"title": _text(candidate.get("title"))},
        head={"sections": {"fulltext": {}}},
        reading_mode=_text(getattr(result, "reading_mode", "")),
        tables=list(getattr(result, "tables", ()) or ()),
        diagnostics={"parser_used": _text(getattr(result, "parser_used", "")), "source_url": _text(getattr(result, "source_url", "")), **dict(getattr(result, "diagnostics", {}) or {})},
    ), None


def _acquire_embedded(candidate: dict[str, Any]) -> tuple[AcquisitionResult | None, dict[str, Any] | None]:
    acquired, error = _admission().acquire_embedded_material(candidate)
    if acquired is None:
        return None, error
    return AcquisitionResult(
        provider=_text(acquired.get("provider")), source_locator=_text(acquired.get("source_locator")),
        material_type=_text(acquired.get("material_type") or "article_fulltext"),
        provider_observed_identity=acquired.get("provider_observed_identity"),
        provider_observed_title=_text(acquired.get("provider_observed_title")),
        sections=list(acquired.get("sections") or []), brief={"title": _text(candidate.get("title"))},
        head={"sections": {s.get("section_name", "section"): {} for s in acquired.get("sections") or [] if isinstance(s, dict)}},
        reading_mode="section", diagnostics={},
    ), None


def _admit(candidate: dict[str, Any], acquisition: AcquisitionResult) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    acquired = {
        "provider": acquisition.provider,
        "source_locator": acquisition.source_locator,
        "provider_observed_title": acquisition.provider_observed_title,
        "provider_observed_identity": acquisition.provider_observed_identity,
        "material_type": acquisition.material_type,
        "sections": acquisition.sections,
    }
    material, rejection = _admission().admit_material(candidate, acquired)
    if material is None:
        return None, rejection
    return material, None


def _source_anchors(candidate_id: str, material: dict[str, Any]) -> list[dict[str, Any]]:
    resolved = material["resolved_identity"]
    identity_key = resolved.get("arxiv_id") or resolved.get("doi") or resolved.get("pmcid") or candidate_id
    anchors: list[dict[str, Any]] = []
    for section in material["sections"]:
        anchors.append({
            "anchor_id": f"anchor:{material['provider']}:{identity_key}:{_slug(section['section_name'])}:{section['content_hash'][:16]}",
            "candidate_id": candidate_id,
            "arxiv_id": resolved.get("arxiv_id", ""),
            "section_name": section["section_name"],
            "content_hash": section["content_hash"],
            "source_type": material["provider"],
            "provider": material["provider"],
            "source_locator": material["source_locator"],
        })
    return anchors


def _packet_from_material(candidate: dict[str, Any], acquisition: AcquisitionResult, material: dict[str, Any], max_sections: int) -> dict[str, Any]:
    candidate_id = _text(candidate.get("candidate_id") or candidate.get("dedupe_key") or "unknown")
    sections = list(material["sections"][:max_sections])
    material = {**material, "sections": sections}
    packet = {
        "candidate_id": candidate_id,
        "arxiv_id": material["resolved_identity"].get("arxiv_id", ""),
        "doi": material["resolved_identity"].get("doi", ""),
        "title": _text(candidate.get("title") or acquisition.provider_observed_title),
        "read_depth": acquisition.reading_mode or "section",
        "reading_mode": acquisition.reading_mode or acquisition.provider,
        "brief": acquisition.brief,
        "head": acquisition.head,
        "sections": sections,
        "source_anchors": _source_anchors(candidate_id, material),
        "material_status": "MATERIAL_READY",
        "material_type": "article_fulltext",
        "requested_identity": material["requested_identity"],
        "provider_observed_identity": material["provider_observed_identity"],
        "resolved_identity": material["resolved_identity"],
        "paper_identity": material["resolved_identity"],
        "provider": material["provider"],
        "source_locator": material["source_locator"],
        "provider_observed_title": acquisition.provider_observed_title,
        "provenance": list(candidate.get("provenance") or []),
        "tables": acquisition.tables[:40],
        "diagnostics": {**acquisition.diagnostics, "identity_basis": material["identity_basis"]},
    }
    return _enrich_table_diagnostics(packet)


def _enrich_table_diagnostics(packet: dict[str, Any]) -> dict[str, Any]:
    mod = _fulltext_module()
    tables = list(packet.get("tables") or [])
    if not tables:
        for section in packet.get("sections") or []:
            content = _text(section.get("content"))
            if "<table" in content.casefold():
                tables.extend(mod.extract_embedded_html_tables(content))
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for table in tables:
        signature = json.dumps((table.get("rows") or [])[:2], ensure_ascii=False)
        if signature not in seen:
            seen.add(signature); unique.append(table)
    body = "\n".join(_text(section.get("content")) for section in packet.get("sections") or [])
    diagnostics = dict(packet.get("diagnostics") or {})
    refs = mod.table_reference_count(body)
    diagnostics.update({"extractable_table_count": len(unique), "table_reference_count": refs, "table_mentioned_but_unavailable": bool(refs and not unique)})
    packet["tables"] = unique[:40]
    packet["diagnostics"] = diagnostics
    return packet


def _abstract_packet(candidate: dict[str, Any]) -> dict[str, Any] | None:
    abstract = _text(candidate.get("abstract") or candidate.get("summary"))
    if not abstract:
        return None
    candidate_id = _text(candidate.get("candidate_id") or candidate.get("dedupe_key") or "unknown")
    requested = _admission().requested_identity(candidate)
    locator = _text(candidate.get("landing_page_url") or candidate.get("source_url") or candidate.get("url")) or f"candidate://{candidate_id}/abstract"
    content_hash = _sha256_text(abstract)
    section = {"section_name": "Abstract", "content": abstract, "content_hash": content_hash, "source_type": "upstream_metadata", "provider": "upstream_metadata", "source_locator": locator, "paper_identity": None, "material_type": "abstract_only", "material_status": "ABSTRACT_ONLY"}
    return {
        "candidate_id": candidate_id, "arxiv_id": requested.get("arxiv_id", ""), "doi": requested.get("doi", ""),
        "title": _text(candidate.get("title")), "read_depth": "abstract_only", "reading_mode": "abstract_only",
        "brief": {"title": _text(candidate.get("title")), "abstract": abstract}, "head": {"sections": {"Abstract": {}}},
        "sections": [section],
        "source_anchors": [{"anchor_id": f"anchor:abstract:{requested.get('doi') or requested.get('arxiv_id') or candidate_id}:{content_hash[:16]}", "candidate_id": candidate_id, "arxiv_id": requested.get("arxiv_id", ""), "section_name": "Abstract", "content_hash": content_hash, "source_type": "upstream_metadata", "provider": "upstream_metadata", "source_locator": locator}],
        "material_status": "ABSTRACT_ONLY", "material_type": "abstract_only", "requested_identity": requested,
        "provider_observed_identity": None, "resolved_identity": None, "paper_identity": None,
        "provider": "upstream_metadata", "source_locator": locator, "provider_observed_title": "",
        "provenance": list(candidate.get("provenance") or []), "tables": [], "diagnostics": {"degraded_reason": "fulltext_unavailable_abstract_only"},
    }


def _attempt_row(candidate: dict[str, Any], reason: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": _text(candidate.get("candidate_id") or candidate.get("dedupe_key") or "unknown"),
        "title": _text(candidate.get("title")),
        "reason": _text(reason.get("reason") or "material_not_ready"),
        "provider_attempted": _text(reason.get("provider_attempted") or reason.get("provider") or "unknown"),
        "diagnostics": reason,
    }


def _partial_section_attempt(candidate: dict[str, Any], acquisition: AcquisitionResult) -> dict[str, Any] | None:
    section_errors = list((acquisition.diagnostics or {}).get("section_errors") or [])
    if not section_errors:
        return None
    return _attempt_row(candidate, {
        "reason": "partial_section_acquisition",
        "provider_attempted": acquisition.provider,
        "section_errors": section_errors,
        "selected_section_names": list((acquisition.diagnostics or {}).get("selected_section_names") or []),
    })


def _read_candidate(candidate: dict[str, Any], *, reader: Any | None, fulltext_reader: Any | None, config: ReadingConfig, evidence_schema: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    attempts: list[dict[str, Any]] = []
    raw_calls = 0

    embedded, embedded_error = _acquire_embedded(candidate)
    explicit_provider = _text(candidate.get("content_provider") or candidate.get("provider")).casefold()
    if embedded is not None:
        material, rejection = _admit(candidate, embedded)
        if material is not None:
            partial = _partial_section_attempt(candidate, embedded)
            if partial is not None:
                attempts.append(partial)
            return _packet_from_material(candidate, embedded, material, config.max_sections_per_paper), attempts, raw_calls
        attempts.append(_attempt_row(candidate, {**(rejection or {}), "provider_attempted": embedded.provider}))
        return None, attempts, raw_calls
    if explicit_provider in {"openalex", "europe_pmc", "europepmc", "core"} and embedded_error and embedded_error.get("reason") not in {"content_fetch_unavailable", "core_inline_fulltext_missing", "core_download_url_only_no_inline_fulltext"}:
        attempts.append(_attempt_row(candidate, {**embedded_error, "provider_attempted": explicit_provider}))
        return None, attempts, raw_calls

    arxiv_id = extract_arxiv_id(candidate)
    if arxiv_id:
        if reader is None:
            attempts.append(_attempt_row(candidate, {"reason": "deepxiv_reader_unavailable", "provider_attempted": "deepxiv"}))
        else:
            acquisition, error, raw_calls = _acquire_deepxiv(candidate, reader, config, evidence_schema)
            if acquisition is not None:
                material, rejection = _admit(candidate, acquisition)
                if material is not None:
                    partial = _partial_section_attempt(candidate, acquisition)
                    if partial is not None:
                        attempts.append(partial)
                    return _packet_from_material(candidate, acquisition, material, config.max_sections_per_paper), attempts, raw_calls
                attempts.append(_attempt_row(candidate, {**(rejection or {}), "provider_attempted": "deepxiv"}))
            elif error:
                attempts.append(_attempt_row(candidate, error))

    if config.enable_non_arxiv_fulltext and fulltext_reader is not None:
        acquisition, error = _acquire_fulltext(candidate, fulltext_reader)
        if acquisition is not None:
            material, rejection = _admit(candidate, acquisition)
            if material is not None:
                partial = _partial_section_attempt(candidate, acquisition)
                if partial is not None:
                    attempts.append(partial)
                return _packet_from_material(candidate, acquisition, material, config.max_sections_per_paper), attempts, raw_calls
            attempts.append(_attempt_row(candidate, {**(rejection or {}), "provider_attempted": acquisition.provider, "material_type": acquisition.material_type}))
        elif error:
            attempts.append(_attempt_row(candidate, error))
    elif not arxiv_id:
        attempts.append(_attempt_row(candidate, {"reason": "non_arxiv_fulltext_disabled", "provider_attempted": "fulltext"}))

    abstract = _abstract_packet(candidate)
    return abstract, attempts, raw_calls


def _apply_payload_budget(
    packets: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    max_total_chars: int,
) -> tuple[list[dict[str, Any]], int]:
    used = 0
    kept_packets: list[dict[str, Any]] = []
    for packet in packets:
        original_sections = list(packet.get("sections") or [])
        kept_sections: list[dict[str, Any]] = []
        for section in original_sections:
            content = _text(section.get("content"))
            if used + len(content) > max_total_chars:
                break
            kept_sections.append(section)
            used += len(content)

        if not kept_sections:
            attempts.append({
                "candidate_id": packet.get("candidate_id", "unknown"),
                "title": packet.get("title", ""),
                "reason": "payload_budget_exhausted",
                "provider_attempted": packet.get("provider", "unknown"),
                "diagnostics": {"max_total_chars": max_total_chars, "sections_kept": 0, "sections_dropped": len(original_sections)},
            })
            continue

        if len(kept_sections) < len(original_sections):
            attempts.append({
                "candidate_id": packet.get("candidate_id", "unknown"),
                "title": packet.get("title", ""),
                "reason": "payload_budget_truncated",
                "provider_attempted": packet.get("provider", "unknown"),
                "diagnostics": {
                    "max_total_chars": max_total_chars,
                    "sections_kept": len(kept_sections),
                    "sections_dropped": len(original_sections) - len(kept_sections),
                },
            })

        packet["sections"] = kept_sections
        if packet.get("material_status") == "MATERIAL_READY":
            material = {
                "resolved_identity": packet["resolved_identity"],
                "provider": packet["provider"],
                "source_locator": packet["source_locator"],
                "sections": kept_sections,
            }
            packet["source_anchors"] = _source_anchors(packet["candidate_id"], material)
        else:
            surviving = {(section.get("section_name"), section.get("content_hash")) for section in kept_sections}
            packet["source_anchors"] = [
                anchor for anchor in packet.get("source_anchors") or []
                if (anchor.get("section_name"), anchor.get("content_hash")) in surviving
            ]
        kept_packets.append(packet)
    return kept_packets, used


def progressive_read_candidates(
    input_data: dict[str, Any],
    *,
    reader: Any | None = None,
    fulltext_reader: Any | None = None,
) -> dict[str, Any]:
    """Execute the single canonical progressive-reading production path."""
    config = _parse_config(input_data)
    all_candidates = _selected_candidates(input_data)
    evidence_schema = _evidence_schema(input_data)
    attempted_candidates = all_candidates[:config.max_papers]
    truncated_count = max(0, len(all_candidates) - len(attempted_candidates))

    needs_deepxiv = any(extract_arxiv_id(candidate) for candidate in attempted_candidates)
    active_reader = reader
    if active_reader is None and needs_deepxiv:
        active_reader = _create_reader(timeout=config.network_timeout_seconds, max_retries=config.max_provider_retries)

    active_fulltext = fulltext_reader
    if active_fulltext is None and config.enable_non_arxiv_fulltext:
        active_fulltext = _create_fulltext_reader(config)

    packets: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    raw_call_count = 0
    for candidate in attempted_candidates:
        packet, candidate_attempts, raw_calls = _read_candidate(candidate, reader=active_reader, fulltext_reader=active_fulltext, config=config, evidence_schema=evidence_schema)
        raw_call_count += raw_calls
        attempts.extend(candidate_attempts)
        if packet is not None:
            packets.append(packet)

    packets, used_chars = _apply_payload_budget(packets, attempts, config.max_total_chars)
    for packet in packets:
        _guidance().annotate_target_reading(packet, evidence_schema)
    required_unresolved = _guidance().required_unresolved_target_ids(packets, evidence_schema)
    ready_count = sum(packet.get("material_status") == "MATERIAL_READY" for packet in packets)
    abstract_count = sum(packet.get("material_status") == "ABSTRACT_ONLY" for packet in packets)
    failed_count = max(0, len(attempted_candidates) - len(packets))
    imperfect = bool(truncated_count or attempts or abstract_count or failed_count or required_unresolved)
    if not packets:
        status = "failed"
    elif imperfect:
        status = "degraded"
    else:
        status = "success"

    section_count = sum(len(packet.get("sections") or []) for packet in packets)
    anchor_count = sum(len(packet.get("source_anchors") or []) for packet in packets)
    return {
        "status": status,
        "paper_packets": packets,
        "material_attempts": attempts,
        "summary": {
            "input_candidate_count": len(all_candidates),
            "attempted_candidate_count": len(attempted_candidates),
            "truncated_candidate_count": truncated_count,
            "paper_packet_count": len(packets),
            "ready_packet_count": ready_count,
            "abstract_only_packet_count": abstract_count,
            "failed_candidate_count": failed_count,
            "material_attempt_count": len(attempts),
            "section_count": section_count,
            "source_anchor_count": anchor_count,
            "partial_success": bool(packets) and imperfect,
            "degraded": status != "success",
        },
        "audit": {
            "skill_id": "deepxiv_progressive_reading",
            "reader_mode": "mixed_deepxiv_and_fulltext" if config.enable_non_arxiv_fulltext else "deepxiv_sdk_reader",
            "raw_default_enabled": config.allow_raw_fallback,
            "raw_call_count": raw_call_count,
            "max_papers": config.max_papers,
            "max_sections_per_paper": config.max_sections_per_paper,
            "network_timeout_seconds": config.network_timeout_seconds,
            "max_provider_retries": config.max_provider_retries,
            "max_total_chars": config.max_total_chars,
            "output_content_chars": used_chars,
            "section_priority": list(config.section_priority),
            "input_fingerprint": _sha256_text(_stable_json({"selected_for_p03": all_candidates, "evidence_schema": evidence_schema})),
        },
    }

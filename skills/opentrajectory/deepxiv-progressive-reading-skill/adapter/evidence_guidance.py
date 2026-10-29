"""EvidenceSchema-driven reading guidance for progressive reading.

This module is deliberately conservative: structured target role/type may guide which
provider sections to read first, while target location is recorded only when phrases
explicitly present in EvidenceSchema are also explicitly present in acquired source text.
It does not perform ResearchRecord extraction or scientific inference.
"""
from __future__ import annotations

import re
from typing import Any, Callable


def _text(value: Any) -> str:
    return "" if value is None else str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _match_text(value: Any) -> str:
    return re.sub(r"[^\w]+", " ", _text(value).casefold(), flags=re.UNICODE).strip()


def validate_evidence_schema(input_data: dict[str, Any]) -> dict[str, Any]:
    schema = input_data.get("evidence_schema")
    if not isinstance(schema, dict):
        raise ValueError("canonical input requires evidence_schema object")
    if "schema_id" not in schema:
        raise ValueError("evidence_schema.schema_id is required")
    for index, target in enumerate(schema.get("evidence_targets") or []):
        if "target_id" not in target:
            raise ValueError(f"evidence_schema.evidence_targets[{index}].target_id is required")
        if "description" not in target:
            raise ValueError(f"evidence_schema.evidence_targets[{index}].description is required")
        if "target_type" not in target or "scientific_role" not in target:
            raise ValueError(f"evidence_schema.evidence_targets[{index}] target_type/scientific_role are required")
    return schema


def section_families(value: str) -> set[str]:
    """Coarse, domain-neutral scientific-section families used only for read ordering."""
    name = _text(value).casefold()
    families: set[str] = set()
    if any(term in name for term in (
        "method", "methodology", "experimental", "experiment", "materials and methods",
        "protocol", "setup", "sample preparation",
    )):
        families.add("methods")
    if any(term in name for term in ("result", "finding", "outcome")):
        families.add("results")
    if any(term in name for term in ("discussion", "interpretation")):
        families.add("discussion")
    if "mechanism" in name:
        families.add("mechanism")
    if any(term in name for term in ("characterization", "measurement", "measurements")):
        families.add("characterization")
    if any(term in name for term in ("theory", "theoretical", "simulation", "calculation", "modeling", "modelling")):
        families.add("theory")
    if any(term in name for term in ("comparison", "baseline", "benchmark")):
        families.add("comparison")
    if any(term in name for term in ("limitation", "limitations")):
        families.add("limitations")
    if any(term in name for term in ("conclusion", "concluding")):
        families.add("conclusion")
    return families


def target_cues(target: dict[str, Any]) -> list[str]:
    """Return only phrases explicitly supplied by EvidenceSchema; no synonym generation."""
    raw = [target.get("description")]
    raw.extend(target.get("condition_dimensions") or [])
    raw.extend(target.get("comparison_requirements") or [])
    cues: list[str] = []
    seen: set[str] = set()
    for value in raw:
        cue = _text(value)
        normalized = _match_text(cue)
        if normalized and normalized not in seen:
            seen.add(normalized)
            cues.append(cue)
    return cues


def _target_family_order(target: dict[str, Any]) -> tuple[str, ...]:
    role_map: dict[str, tuple[str, ...]] = {
        "composition": ("methods", "results"),
        "condition": ("methods",),
        "metric": ("results", "discussion"),
        "mechanism": ("mechanism", "discussion", "results"),
        "characterization": ("characterization", "results", "methods"),
        "theoretical_explanation": ("theory", "discussion", "results"),
        "baseline": ("comparison", "results", "methods"),
        "comparability": ("comparison", "methods", "results", "discussion"),
        "limitation": ("limitations", "discussion", "conclusion"),
        "provenance": ("methods",),
        "other": (),
    }
    type_map: dict[str, tuple[str, ...]] = {
        "experimental_condition": ("methods",),
        "performance_metric": ("results", "discussion"),
        "mechanism_evidence": ("mechanism", "discussion", "results"),
        "characterization_observation": ("characterization", "results"),
        "theoretical_calculation": ("theory", "results"),
        "comparison_baseline": ("comparison", "results", "methods"),
        "limitation": ("limitations", "discussion"),
    }
    ordered: list[str] = []
    if target.get("condition_dimensions"):
        ordered.append("methods")
    if target.get("comparison_requirements"):
        ordered.extend(("comparison", "results", "methods", "discussion"))
    ordered.extend(role_map.get(_text(target.get("scientific_role")), ()))
    ordered.extend(type_map.get(_text(target.get("target_type")), ()))
    return tuple(dict.fromkeys(ordered))


def _ordered_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {"required": 0, "preferred": 1, "optional": 2}
    return sorted(
        targets,
        key=lambda target: (rank.get(str(target.get("requiredness")), 3), str(target.get("target_id"))),
    )


def section_order(
    available: list[str],
    default_order: list[str],
    targets: list[dict[str, Any]],
) -> list[str]:
    """Order already-admissible provider sections from EvidenceSchema then generic defaults."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            ordered.append(name)

    for target in _ordered_targets(targets):
        normalized_cues = [_match_text(cue) for cue in target_cues(target)]
        for name in available:
            normalized_name = _match_text(name)
            if any(cue and cue in normalized_name for cue in normalized_cues):
                add(name)

    for target in _ordered_targets(targets):
        for family in _target_family_order(target):
            for name in available:
                if family in section_families(name):
                    add(name)
                    break

    for name in default_order:
        add(name)
    for name in available:
        add(name)
    return ordered


def goal_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    goals = [target for target in targets if target.get("requiredness") in {"required", "preferred"}]
    return goals or list(targets)


def target_match(
    target: dict[str, Any],
    sections: list[dict[str, Any]],
) -> tuple[list[str], list[str], set[int]]:
    cues = target_cues(target)
    matched: list[str] = []
    matched_sections: set[int] = set()
    normalized_sections = [_match_text(section.get("content")) for section in sections]
    for cue in cues:
        normalized_cue = _match_text(cue)
        hit_indexes = {
            index for index, content in enumerate(normalized_sections)
            if normalized_cue and normalized_cue in content
        }
        if hit_indexes:
            matched.append(cue)
            matched_sections.update(hit_indexes)
    matched_norm = {_match_text(cue) for cue in matched}
    missing = [cue for cue in cues if _match_text(cue) not in matched_norm]
    return matched, missing, matched_sections


def goals_located(goals: list[dict[str, Any]], sections: list[dict[str, Any]]) -> bool:
    return bool(goals) and all(not target_match(target, sections)[1] for target in goals)


def annotate_target_reading(packet: dict[str, Any], evidence_schema: dict[str, Any]) -> None:
    sections = list(packet.get("sections") or [])
    anchors = list(packet.get("source_anchors") or [])
    anchor_by_section: dict[tuple[str, str], list[str]] = {}
    for anchor in anchors:
        key = (_text(anchor.get("section_name")), _text(anchor.get("content_hash")))
        anchor_by_section.setdefault(key, []).append(_text(anchor.get("anchor_id")))

    results: list[dict[str, Any]] = []
    for target in evidence_schema["evidence_targets"]:
        matched, missing, matched_indexes = target_match(target, sections)
        anchor_ids: list[str] = []
        seen_anchor_ids: set[str] = set()
        for index in sorted(matched_indexes):
            section = sections[index]
            key = (_text(section.get("section_name")), _text(section.get("content_hash")))
            for anchor_id in anchor_by_section.get(key, []):
                if anchor_id and anchor_id not in seen_anchor_ids:
                    seen_anchor_ids.add(anchor_id)
                    anchor_ids.append(anchor_id)
        results.append({
            "target_id": _text(target.get("target_id")),
            "target_type": _text(target.get("target_type")),
            "scientific_role": _text(target.get("scientific_role")),
            "requiredness": _text(target.get("requiredness")),
            "status": "located_in_read_content" if not missing else "unresolved",
            "matched_cues": matched,
            "missing_cues": missing,
            "source_anchor_ids": anchor_ids,
        })

    diagnostics = dict(packet.get("diagnostics") or {})
    diagnostics.update({
        "evidence_schema_id": _text(evidence_schema.get("schema_id")),
        "evidence_schema_version": _text(evidence_schema.get("schema_version")),
        "target_reading": results,
    })
    packet["diagnostics"] = diagnostics


def required_unresolved_target_ids(
    packets: list[dict[str, Any]],
    evidence_schema: dict[str, Any],
) -> list[str]:
    required_ids = [
        _text(target.get("target_id"))
        for target in evidence_schema["evidence_targets"]
        if target.get("requiredness") == "required"
    ]
    resolved_ids: set[str] = set()
    for packet in packets:
        for result in (packet.get("diagnostics") or {}).get("target_reading") or []:
            if result.get("status") == "located_in_read_content":
                resolved_ids.add(_text(result.get("target_id")))
    return [target_id for target_id in required_ids if target_id not in resolved_ids]

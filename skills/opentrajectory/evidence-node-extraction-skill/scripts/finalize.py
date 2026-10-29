"""Deterministic finalization for ``evidence_node_extraction`` ResearchRecords.

The model owns scientific semantics at condition-detail granularity. This module
derives the aggregate condition status and otherwise only enforces deterministic
source binding, stable identity, deduplication, summary/audit metadata, and
required-key structure. Scientific descriptions and values remain model-owned.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator

SKILL_ID = "evidence_node_extraction"
FINALIZER_VERSION = "2.2.0"
_SUPPORTED_RECORD_TYPES = (
    "material_composition",
    "experimental_condition",
    "quantitative_result",
    "characterization_observation",
    "computation_result",
    "comparison",
    "mechanism_statement",
    "limitation",
    "qualitative_result",
)

_SKILL_DIR = Path(__file__).resolve().parents[1]
_MODEL_SCHEMA = json.loads((_SKILL_DIR / "model_output.schema.json").read_text(encoding="utf-8"))
_FINAL_SCHEMA = json.loads((_SKILL_DIR / "output.schema.json").read_text(encoding="utf-8"))
_MODEL_VALIDATOR = Draft7Validator(_MODEL_SCHEMA)
_FINAL_VALIDATOR = Draft7Validator(_FINAL_SCHEMA)


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[Any, ...] = ()


@dataclass(frozen=True)
class _AnchorContext:
    packet: Mapping[str, Any]
    anchor: Mapping[str, Any]
    section: Mapping[str, Any]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_ws(value: str) -> str:
    return " ".join(value.split())


def _validation_errors(validator: Draft7Validator, value: Any) -> list[str]:
    errors = sorted((error for error in validator.iter_errors(value) if error.validator == "required"), key=lambda exc: list(exc.absolute_path))
    return [
        f"schema:{'.'.join(str(part) for part in exc.absolute_path) or '$'}:{exc.message}"
        for exc in errors
    ]


def _build_anchor_index(input_payload: Mapping[str, Any]) -> tuple[dict[str, _AnchorContext], list[str]]:
    anchors: dict[str, _AnchorContext] = {}
    for packet in input_payload.get("paper_packets") or []:
        sections = packet.get("sections") or []
        for anchor in packet.get("source_anchors") or []:
            section = next((row for row in sections if row.get("section_name") == anchor.get("section_name")), {})
            anchors[str(anchor.get("anchor_id") or "")] = _AnchorContext(packet, anchor, section)
    return anchors, []


def _target_contract(input_payload: Mapping[str, Any]) -> tuple[set[str], set[str], str | None, list[str]]:
    schema = input_payload["evidence_schema"]
    targets = schema["evidence_targets"]
    target_ids = {str(row.get("target_id") or "") for row in targets}
    required = {str(row.get("target_id") or "") for row in targets if row.get("requiredness") == "required"}
    return target_ids, required, str(schema.get("schema_id") or "") or None, []


def _record_id(row: Mapping[str, Any]) -> str:
    return "rr:" + sha256(_canonical_json(row).encode("utf-8")).hexdigest()[:32]


def _source_identity(context: _AnchorContext) -> dict[str, Any]:
    packet = context.packet
    anchor = context.anchor
    return {
        "candidate_id": str(packet.get("candidate_id") or anchor.get("candidate_id") or ""),
        "anchor_id": str(anchor.get("anchor_id") or ""),
        "title": str(packet.get("title") or ""),
        "doi": str(packet.get("doi") or ""),
        "arxiv_id": str(packet.get("arxiv_id") or anchor.get("arxiv_id") or ""),
        "provider": str(anchor.get("provider") or packet.get("provider") or ""),
        "source_locator": str(anchor.get("source_locator") or packet.get("source_locator") or ""),
        "section_name": str(anchor.get("section_name") or ""),
        "content_hash": str(anchor.get("content_hash") or "").lower(),
        "source_type": str(anchor.get("source_type") or context.section.get("source_type") or ""),
        "provenance": deepcopy(packet.get("provenance") or []),
    }


def _condition_status(conditions: list[Mapping[str, Any]]) -> str:
    if not conditions:
        return "not_applicable"
    if any(condition["status"] == "unknown" for condition in conditions):
        return "partially_unknown"
    return "reported"


def _finalize_row(row: Mapping[str, Any], context: _AnchorContext) -> dict[str, Any]:
    semantic = deepcopy(dict(row))
    semantic["source_quote"] = _normalize_ws(str(semantic["source_quote"]))
    semantic["condition_status"] = _condition_status(semantic["conditions"])
    return {
        "record_id": _record_id(semantic),
        "source": _source_identity(context),
        **semantic,
    }


def _is_degraded(context: _AnchorContext) -> bool:
    material_status = str(context.packet.get("material_status") or "").casefold()
    read_depth = str(context.packet.get("read_depth") or "").casefold()
    source_type = str(context.anchor.get("source_type") or "").casefold()
    return material_status == "abstract_only" or read_depth == "raw_fallback" or "abstract" in source_type or "raw" in source_type


def _dedupe_key(row: Mapping[str, Any]) -> str:
    semantic = {key: value for key, value in row.items() if key not in {"record_id", "source"}}
    return _canonical_json(semantic)


def _finalize_rows(
    rows: list[Mapping[str, Any]],
    anchors: Mapping[str, _AnchorContext],
    target_ids: set[str],
) -> tuple[list[dict[str, Any]], list[Any], int, bool]:
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped_duplicate_count = 0
    degraded = False
    for row in rows:
        anchor_id = str(row.get("source_anchor_id") or "")
        context = anchors.get(anchor_id, _AnchorContext({}, {"anchor_id": anchor_id}, {}))
        finalized = _finalize_row(row, context)
        key = _dedupe_key(finalized)
        if key in seen:
            dropped_duplicate_count += 1
            continue
        seen.add(key)
        accepted.append(finalized)
        degraded = degraded or _is_degraded(context)
    return accepted, [], dropped_duplicate_count, degraded


def _result_status(
    accepted: list[Mapping[str, Any]],
    invalid_errors: list[Any],
    uncovered_required: list[str],
    degraded: bool,
) -> str:
    if accepted and (invalid_errors or uncovered_required):
        return "partial"
    if accepted and degraded:
        return "degraded"
    return "succeeded"


def _warnings(
    invalid_errors: list[Any],
    dropped_duplicate_count: int,
    uncovered_required: list[str],
    degraded: bool,
) -> list[str]:
    warnings: list[str] = []
    if invalid_errors:
        warnings.append(f"dropped_invalid_record:{len(invalid_errors)}")
    if dropped_duplicate_count:
        warnings.append(f"dropped_duplicate_record:{dropped_duplicate_count}")
    warnings.extend(f"required_target_uncovered:{target_id}" for target_id in uncovered_required)
    if degraded:
        warnings.append("degraded_source_scope")
    return warnings


def _build_output(
    input_payload: Mapping[str, Any],
    accepted: list[dict[str, Any]],
    schema_id: str | None,
    status: str,
    anchors: Mapping[str, _AnchorContext],
    invalid_errors: list[Any],
    dropped_duplicate_count: int,
    uncovered_required: list[str],
    degraded: bool,
) -> dict[str, Any]:
    counts = Counter(row["record_type"] for row in accepted)
    return {
        "research_records": accepted,
        "schema_id": schema_id,
        "summary": {
            "status": status,
            "input_paper_packet_count": len(input_payload.get("paper_packets") or []),
            "input_source_anchor_count": len(anchors),
            "research_record_count": len(accepted),
            "record_type_counts": dict(sorted(counts.items())),
            "dropped_duplicate_count": dropped_duplicate_count,
            "dropped_invalid_count": len(invalid_errors),
            "uncovered_required_target_ids": uncovered_required,
            "degraded": degraded,
        },
        "audit": {
            "skill_id": SKILL_ID,
            "traceability_mode": "research_record_source_anchor_quote_finalizer",
            "finalizer_version": FINALIZER_VERSION,
            "input_fingerprint": _fingerprint(input_payload),
            "supported_record_types": list(_SUPPORTED_RECORD_TYPES),
        },
        "extraction_warnings": _warnings(
            invalid_errors,
            dropped_duplicate_count,
            uncovered_required,
            degraded,
        ),
    }


def finalize(
    input_payload: Mapping[str, Any],
    model_output: Mapping[str, Any],
) -> FinalizationResult:
    """Finalize one semantic ResearchRecord draft without scientific reinterpretation."""
    if not isinstance(input_payload, Mapping):
        return FinalizationResult("failed", None, ("input:not_object",))
    if not isinstance(model_output, Mapping):
        return FinalizationResult("failed", None, ("model_output:not_object",))

    model_errors = _validation_errors(_MODEL_VALIDATOR, model_output)
    if model_errors:
        return FinalizationResult("failed", None, tuple(model_errors))

    anchors, input_errors = _build_anchor_index(input_payload)
    target_ids, required_targets, schema_id, target_errors = _target_contract(input_payload)
    input_errors.extend(target_errors)
    if input_errors:
        return FinalizationResult("failed", None, tuple(input_errors))

    rows = model_output["research_records"]
    accepted, invalid_errors, dropped_duplicate_count, degraded = _finalize_rows(rows, anchors, target_ids)
    if rows and not accepted:
        return FinalizationResult("failed", None, tuple(invalid_errors or ["all_records_invalid"]))

    covered_targets = {
        target_id
        for row in accepted
        for target_id in row.get("relevant_target_ids") or []
    }
    uncovered_required = sorted(required_targets - covered_targets)
    status = _result_status(accepted, invalid_errors, uncovered_required, degraded)
    output = _build_output(
        input_payload,
        accepted,
        schema_id,
        status,
        anchors,
        invalid_errors,
        dropped_duplicate_count,
        uncovered_required,
        degraded,
    )

    final_errors = _validation_errors(_FINAL_VALIDATOR, output)
    if final_errors:
        return FinalizationResult("failed", None, tuple(final_errors))
    return FinalizationResult(status, output, tuple(invalid_errors))

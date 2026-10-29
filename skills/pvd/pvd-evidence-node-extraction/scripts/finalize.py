from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator

SKILL_ID = "pvd_evidence_node_extraction"
FINALIZER_VERSION = "1.1.1-openair-t04"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_SCHEMA = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
OUTPUT_VALIDATOR = Draft7Validator(OUTPUT_SCHEMA)
_OUTPUT_ITEM_SCHEMA = OUTPUT_SCHEMA["properties"]["evidence_items"]["items"]
SUPPORTED_EVIDENCE_TYPES = tuple(
    _OUTPUT_ITEM_SCHEMA["properties"]["evidence_type"]["enum"]
)
_MODEL_OWNED_FIELDS = (
    "source_anchor_id",
    "evidence_type",
    "assertion_mode",
    "claim",
    "evidence_quote",
    "conditions",
    "metric",
    "mechanism",
    "comparison",
    "qualifiers",
    "confidence",
    "matched_target_ids",
    "target_match_status",
)


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    canonical = _canonical_json(value)
    try:
        payload = canonical.encode("utf-8")
    except UnicodeEncodeError:
        payload = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    return sha256(payload).hexdigest()


def _schema_errors(validator: Draft7Validator, value: Any, prefix: str) -> list[str]:
    errors = sorted(
        (error for error in validator.iter_errors(value) if error.validator == "required"),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    return [
        f"{prefix}:/{'/'.join(map(str, error.absolute_path))}:{error.message}"
        for error in errors[:40]
    ]




def _source_indexes(paper_packets):
    anchors = {}
    sections_by_anchor = {}
    for packet in paper_packets:
        sections = packet.get("sections") or []
        for anchor in packet.get("source_anchors") or []:
            anchor_id = anchor.get("anchor_id")
            anchors[anchor_id] = anchor
            sections_by_anchor[anchor_id] = next(
                (section for section in sections if section.get("section_name") == anchor.get("section_name")), {}
            )
    return anchors, sections_by_anchor, []


def _required_targets(evidence_schema: Mapping[str, Any]) -> set[str]:
    return {
        row["target_id"]
        for row in evidence_schema.get("evidence_targets") or []
        if isinstance(row, Mapping)
        and row.get("requiredness") == "required"
        and isinstance(row.get("target_id"), str)
    }




def _finalize_item(
    raw: Mapping[str, Any],
    *,
    anchors: Mapping[str, Mapping[str, Any]],
    sections_by_anchor: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    anchor_id = raw.get("source_anchor_id")
    anchor = anchors.get(anchor_id) or {}
    semantic = {
        field: deepcopy(raw[field])
        for field in _MODEL_OWNED_FIELDS
        if field in raw and not (field == "target_match_status" and raw[field] is None)
    }
    stable_payload = {
        "source_anchor_id": anchor_id,
        "evidence_type": semantic.get("evidence_type"),
        "assertion_mode": semantic.get("assertion_mode"),
        "claim": semantic.get("claim"),
        "evidence_quote": semantic.get("evidence_quote"),
        "conditions": semantic.get("conditions"),
        "metric": semantic.get("metric"),
        "mechanism": semantic.get("mechanism"),
        "comparison": semantic.get("comparison"),
        "matched_target_ids": semantic.get("matched_target_ids"),
    }
    semantic.update(
        {
            "evidence_id": "ev:" + _fingerprint(stable_payload)[:32],
            "candidate_id": str(anchor.get("candidate_id") or raw.get("candidate_id") or ""),
            "arxiv_id": str(anchor.get("arxiv_id") or raw.get("arxiv_id") or ""),
            "section_name": str(anchor.get("section_name") or raw.get("section_name") or ""),
            "content_hash": str(anchor.get("content_hash") or raw.get("content_hash") or ""),
        }
    )
    return semantic, None


def finalize(
    input_payload: Mapping[str, Any],
    model_output: Mapping[str, Any],
) -> FinalizationResult:
    paper_packets = input_payload.get("paper_packets")
    evidence_schema = input_payload.get("evidence_schema")
    if not isinstance(paper_packets, list) or not paper_packets:
        return FinalizationResult("failed", None, ("input:paper_packets_required",))
    if not isinstance(evidence_schema, Mapping):
        return FinalizationResult("failed", None, ("input:evidence_schema_required",))

    packet_rows = [row for row in paper_packets if isinstance(row, Mapping)]
    if len(packet_rows) != len(paper_packets):
        return FinalizationResult("failed", None, ("input:paper_packets_invalid",))
    anchors, sections_by_anchor, source_errors = _source_indexes(packet_rows)
    if source_errors:
        return FinalizationResult("failed", None, tuple(source_errors))

    evidence_items = model_output.get("evidence_items")
    if not isinstance(evidence_items, list):
        return FinalizationResult(
            "failed", None, ("model:evidence_items_must_be_array",)
        )

    finalized: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    isolation_warnings: list[str] = []
    dropped_invalid_count = 0
    dropped_duplicate_count = 0
    for index, raw in enumerate(evidence_items):
        if not isinstance(raw, Mapping):
            dropped_invalid_count += 1
            isolation_warnings.append(f"evidence_item_unavailable:item_index={index + 1}:reason=not_object")
            continue
        item, error = _finalize_item(
            raw,
            anchors=anchors,
            sections_by_anchor=sections_by_anchor,
        )
        if error or item is None:
            dropped_invalid_count += 1
            isolation_warnings.append(f"evidence_item_unavailable:item_index={index + 1}:reason={error or 'invalid'}")
            continue
        evidence_id = item["evidence_id"]
        if evidence_id in seen_evidence_ids:
            dropped_duplicate_count += 1
            isolation_warnings.append(f"evidence_item_unavailable:item_index={index + 1}:reason=duplicate")
            continue
        seen_evidence_ids.add(evidence_id)
        finalized.append(item)

    covered_targets = {
        target_id
        for item in finalized
        for target_id in (item.get("matched_target_ids") or [])
    }
    uncovered_required = sorted(_required_targets(evidence_schema) - covered_targets)
    degraded = bool(uncovered_required or isolation_warnings)
    counts = Counter(str(item["evidence_type"]) for item in finalized)
    output: dict[str, Any] = {
        "evidence_items": finalized,
        "schema_id": (
            str(evidence_schema.get("schema_id") or "").strip()
            or "pvd-evidence-schema:" + _fingerprint(evidence_schema)[:24]
        ),
        "summary": {
            "status": "degraded" if degraded else "succeeded",
            "input_paper_packet_count": len(paper_packets),
            "input_source_anchor_count": len(anchors),
            "evidence_item_count": len(finalized),
            "evidence_type_counts": dict(sorted(counts.items())),
            "dropped_duplicate_count": dropped_duplicate_count,
            "dropped_invalid_count": dropped_invalid_count,
            "uncovered_required_target_ids": uncovered_required,
            "degraded": degraded,
        },
        "audit": {
            "skill_id": SKILL_ID,
            "traceability_mode": "source_anchor_quote_finalizer",
            "finalizer_version": FINALIZER_VERSION,
            "input_fingerprint": _fingerprint(input_payload),
            "supported_evidence_types": list(SUPPORTED_EVIDENCE_TYPES),
        },
        "extraction_warnings": sorted(set(isolation_warnings)),
    }
    output_errors = _schema_errors(OUTPUT_VALIDATOR, output, "output_schema")
    if output_errors:
        return FinalizationResult("failed", None, tuple(output_errors))
    return FinalizationResult("succeeded", output, ())

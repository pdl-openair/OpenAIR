"""Deterministic finalization for PVD Research Record Structuring.

The model owns scientific grouping and semantic mapping. This module owns only
required-key validation, source/evidence binding, deterministic identity,
and conservative missing-state completion.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator, Draft202012Validator
from referencing import Registry, Resource

SKILL_ID = "pvd_research_record_structuring"
FINALIZER_VERSION = "1.0.1"
ROOT = Path(__file__).resolve().parents[1]
INPUT_SCHEMA = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))
EVIDENCE_SCHEMA = INPUT_SCHEMA["properties"]["evidence_packet"]
SOURCE_SCHEMA = INPUT_SCHEMA["properties"]["source_material"]
MODEL_SCHEMA = json.loads((ROOT / "model_output.schema.json").read_text(encoding="utf-8"))
OUTPUT_SCHEMA = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))

EVIDENCE_VALIDATOR = Draft7Validator(EVIDENCE_SCHEMA)
SOURCE_VALIDATOR = Draft7Validator(SOURCE_SCHEMA)
MODEL_REGISTRY = Registry().with_resource(
    str(OUTPUT_SCHEMA["$id"]),
    Resource.from_contents(OUTPUT_SCHEMA),
)
MODEL_VALIDATOR = Draft202012Validator(MODEL_SCHEMA, registry=MODEL_REGISTRY)
OUTPUT_VALIDATOR = Draft202012Validator(OUTPUT_SCHEMA)

MATERIAL_FACT_KEYS = (
    "target_system", "target_composition", "film_composition", "substrate",
    "electrode", "buffer", "film_structure",
)
PROCESS_FACT_KEYS = (
    "sputtering_mode", "power", "power_density", "n2_flow", "ar_flow",
    "gas_ratio", "reactive_gas_fraction", "total_flow", "pressure",
    "reactive_gas_partial_pressure", "target_substrate_distance",
    "substrate_temperature", "deposition_time", "film_thickness",
    "substrate_bias", "target_effective_area", "target_geometry",
    "chamber_geometry", "base_pressure", "substrate_rotation", "post_treatment",
)
MEASUREMENT_FACT_KEYS = (
    "measurement_method", "measurement_geometry", "sign_convention", "correction_state",
)


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _schema_errors(validator: Any, value: Any, prefix: str) -> list[str]:
    errors = sorted((error for error in validator.iter_errors(value) if error.validator == "required"), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{prefix}:/{'/'.join(map(str, e.absolute_path))}:{e.message}" for e in errors[:30]]








def _index_source_material(source_material):
    packets = {str(packet.get("candidate_id") or ""): packet for packet in source_material.get("paper_packets") or []}
    anchors = {str(anchor.get("anchor_id") or ""): anchor for packet in packets.values() for anchor in packet.get("source_anchors") or []}
    return packets, anchors, []


def _index_evidence(evidence_packet):
    return {str(item.get("evidence_id") or ""): item for item in evidence_packet.get("evidence_items") or []}, []










def _unknown_fact() -> dict[str, Any]:
    return {"fact_status": "unknown"}


def _complete_fact_sections(group: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    material = deepcopy(group.get("material") or {})
    process = deepcopy(group.get("process") or {})
    for key in MATERIAL_FACT_KEYS:
        material.setdefault(key, _unknown_fact())
    material.setdefault("other_conditions", [])
    for key in PROCESS_FACT_KEYS:
        process.setdefault(key, _unknown_fact())
    process.setdefault("other_conditions", [])
    return material, process


def _record_id(candidate_id: str, group: Mapping[str, Any]) -> str:
    identity = {
        "candidate_id": candidate_id,
        "evidence_refs": sorted(group.get("evidence_refs") or []),
        "study_id": group.get("study_id"),
        "series_id": group.get("series_id"),
        "sample_id": group.get("sample_id"),
        "record_role": group.get("record_role"),
    }
    return "pvdrec:" + _fingerprint(identity)[:24]


def _source_row(packet: Mapping[str, Any]) -> dict[str, Any]:
    source_id = str(packet.get("candidate_id") or "")
    row: dict[str, Any] = {"source_id": source_id, "source_type": "other"}
    title = str(packet.get("title") or "").strip()
    doi = str(packet.get("doi") or "").strip()
    locator = str(packet.get("source_locator") or "").strip()
    if title:
        row["citation"] = title
    if doi:
        row["doi"] = doi
    if locator:
        row["source_location"] = locator
    if packet.get("material_status") == "MATERIAL_READY":
        row["access_state"] = "verified"
    return row


def _build_output(groups: list[Mapping[str, Any]], evidence: Mapping[str, Mapping[str, Any]], packets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    for group in groups:
        refs = list(group.get("evidence_refs") or [])
        first = next((evidence[ref] for ref in refs if ref in evidence), {})
        source_id = str(first.get("candidate_id") or group.get("source_id") or "")
        used_sources.add(source_id)
        material, process = _complete_fact_sections(group)
        record: dict[str, Any] = {
            "record_id": _record_id(source_id, group),
            "source_id": source_id,
            "record_role": group.get("record_role") or "explicit_research_record",
            "material": material,
            "process": process,
            "outcomes": deepcopy(group.get("outcomes") or []),
            "other_context": deepcopy(group.get("other_context") or []),
            "provenance_refs": sorted(refs),
        }
        for key in ("study_id", "series_id", "sample_id"):
            if group.get(key):
                record[key] = group[key]
        records.append(record)
    records.sort(key=lambda row: row["record_id"])
    provenance = [_source_row(packets.get(source_id) or {"candidate_id": source_id}) for source_id in sorted(used_sources)]
    return {"evidence_records": records, "source_provenance": provenance}


def _singleton_known_fact(
    value: Any,
    evidence_id: str,
    *,
    unit: Any = None,
) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "fact_status": "known",
        "value": deepcopy(value),
        "provenance_refs": [evidence_id],
    }
    if isinstance(unit, str) and unit.strip():
        fact["unit"] = unit
    return fact


def _singleton_group_from_evidence(item: Mapping[str, Any]) -> dict[str, Any]:
    evidence_id = str(item.get("evidence_id") or "")
    other_context: list[dict[str, Any]] = []

    claim = item.get("claim")
    if isinstance(claim, str) and claim.strip():
        other_context.append(
            {
                "key": "evidence_claim",
                "fact": _singleton_known_fact(claim, evidence_id),
            }
        )
    quote = item.get("evidence_quote")
    if isinstance(quote, str) and quote.strip():
        other_context.append(
            {
                "key": "evidence_quote",
                "fact": _singleton_known_fact(quote, evidence_id),
            }
        )
    for index, condition in enumerate(item.get("conditions") or []):
        if not isinstance(condition, Mapping):
            continue
        name = str(condition.get("name") or "").strip() or f"condition_{index + 1}"
        value = condition.get("value_text")
        if not isinstance(value, str) or not value.strip():
            continue
        other_context.append(
            {
                "key": name,
                "fact": _singleton_known_fact(
                    value, evidence_id, unit=condition.get("unit")
                ),
            }
        )

    outcomes: list[dict[str, Any]] = []
    metric = item.get("metric")
    if isinstance(metric, Mapping):
        metric_name = metric.get("name")
        metric_value = metric.get("value_text")
        if (
            isinstance(metric_name, str)
            and metric_name.strip()
            and isinstance(metric_value, str)
            and metric_value.strip()
        ):
            measurement: dict[str, Any] = {
                "metric": metric_name,
                "value": metric_value,
                "provenance_refs": [evidence_id],
            }
            unit = metric.get("unit")
            if isinstance(unit, str) and unit.strip():
                measurement["unit"] = unit
            outcomes.append(measurement)

    return {
        "group_id": f"evidence-singleton:{evidence_id}",
        "grouping_status": "ambiguous_singleton",
        "evidence_refs": [evidence_id],
        "grouping_basis_evidence_refs": [evidence_id],
        "record_role": "unresolved_record_identity",
        "outcomes": outcomes,
        "other_context": other_context,
    }


def materialize_evidence_singletons(input_payload: Mapping[str, Any]) -> FinalizationResult:
    """Deterministic grouping degradation that keeps every valid Evidence item usable."""

    evidence_packet = input_payload.get("evidence_packet")
    source_material = input_payload.get("source_material")
    if not isinstance(evidence_packet, Mapping) or not isinstance(source_material, Mapping):
        return FinalizationResult("failed", None, ("input:missing_required_artifact",))

    errors = _schema_errors(EVIDENCE_VALIDATOR, evidence_packet, "evidence_schema")
    errors.extend(_schema_errors(SOURCE_VALIDATOR, source_material, "source_schema"))
    if errors:
        return FinalizationResult("failed", None, tuple(errors))

    packets, anchors, source_errors = _index_source_material(source_material)
    evidence, evidence_errors = _index_evidence(evidence_packet)
    errors.extend(source_errors)
    errors.extend(evidence_errors)
    if errors:
        return FinalizationResult("failed", None, tuple(errors))
    if not evidence:
        return FinalizationResult(
            "succeeded",
            {"evidence_records": [], "source_provenance": []},
            (),
        )

    groups = [
        _singleton_group_from_evidence(evidence[evidence_id])
        for evidence_id in sorted(evidence)
    ]
    output = _build_output(groups, evidence, packets)
    out_errors = _schema_errors(OUTPUT_VALIDATOR, output, "output_schema")
    if out_errors:
        return FinalizationResult("failed", None, tuple(out_errors))
    return FinalizationResult("degraded", output, ())


def finalize(input_payload: Mapping[str, Any], model_output: Mapping[str, Any]) -> FinalizationResult:
    evidence_packet = input_payload.get("evidence_packet")
    source_material = input_payload.get("source_material")
    if not isinstance(evidence_packet, Mapping) or not isinstance(source_material, Mapping):
        return FinalizationResult("failed", None, ("input:missing_required_artifact",))

    errors = _schema_errors(EVIDENCE_VALIDATOR, evidence_packet, "evidence_schema")
    errors.extend(_schema_errors(SOURCE_VALIDATOR, source_material, "source_schema"))
    if errors:
        return FinalizationResult("failed", None, tuple(errors))

    packets, anchors, source_errors = _index_source_material(source_material)
    evidence, evidence_errors = _index_evidence(evidence_packet)
    errors.extend(source_errors)
    errors.extend(evidence_errors)
    if errors:
        return FinalizationResult("failed", None, tuple(errors))

    model_errors = _schema_errors(MODEL_VALIDATOR, model_output, "model_schema")
    if model_errors:
        return FinalizationResult("failed", None, tuple(model_errors))
    groups = list(model_output.get("record_groups") or [])
    output = _build_output(groups, evidence, packets)
    out_errors = _schema_errors(OUTPUT_VALIDATOR, output, "output_schema")
    if out_errors:
        return FinalizationResult("failed", None, tuple(out_errors))
    return FinalizationResult("succeeded", output, ())


def assert_finalized(input_payload: Mapping[str, Any], model_output: Mapping[str, Any]) -> dict[str, Any]:
    result = finalize(input_payload, model_output)
    if result.output is None:
        raise ValueError("PVD Research Record Structuring finalization failed: " + " | ".join(result.errors))
    return result.output

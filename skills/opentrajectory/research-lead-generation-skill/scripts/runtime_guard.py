from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft7Validator

from scripts.identity import build_input_fingerprint, build_research_lead_id

GUARD_VERSION = "s1-lead-v4"
SKILL_ID = "ot_research_lead_generation"
NOVELTY_BOUNDARY = "research_direction_not_hypothesis_or_novelty_claim"
WITHIN_RESEARCH_LEAD_BOUNDARY = "within_research_lead_boundary"
DEFAULT_MAX_RESEARCH_LEADS = 8

ROOT = Path(__file__).resolve().parents[1]


class ContractRejected(ValueError):
    pass


def _load_validator(filename: str) -> Draft7Validator:
    import json

    schema = json.loads((ROOT / filename).read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


INPUT_VALIDATOR = _load_validator("input.schema.json")
DRAFT_VALIDATOR = _load_validator("draft.schema.json")
OUTPUT_VALIDATOR = _load_validator("output.schema.json")


def _validate_schema(validator: Draft7Validator, payload: Any, label: str) -> None:
    errors = sorted(
        (error for error in validator.iter_errors(payload) if error.validator == "required"),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    raise ContractRejected(f"{label} schema violation at {path}: {error.message}")


def validate_draft(draft_payload: Any) -> dict[str, Any]:
    _validate_schema(DRAFT_VALIDATOR, draft_payload, "draft")
    return copy.deepcopy(cast(dict[str, Any], draft_payload))


def _index_by_unique_id(
    items: list[dict[str, Any]],
    *,
    id_field: str,
    item_label: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = item[id_field]
        indexed[item_id] = item
    return indexed


def _build_indexes(
    input_payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    mechanism_packet = input_payload.get("mechanism_packet")
    edges = _index_by_unique_id(
        mechanism_packet["mechanism_edges"] if mechanism_packet is not None else [],
        id_field="edge_id",
        item_label="mechanism edge",
    )
    findings = _index_by_unique_id(
        input_payload["findings_packet"]["findings"],
        id_field="finding_id",
        item_label="finding",
    )
    return edges, findings


def _canonicalize_references(lead: dict[str, Any]) -> dict[str, Any]:
    canonical = copy.deepcopy(lead)
    for field in (
        "source_finding_ids",
        "mechanism_basis_edge_ids",
        "gap_edge_ids",
        "provenance_evidence_ids",
    ):
        canonical[field] = sorted(canonical[field])
    return canonical


def finalize_lead(lead: dict[str, Any]) -> dict[str, Any]:
    finalized = _canonicalize_references(lead)
    finalized["research_lead_id"] = build_research_lead_id(finalized)
    finalized["claim_type"] = "research_lead"
    finalized["novelty_boundary"] = NOVELTY_BOUNDARY
    return finalized


def _max_research_leads(input_payload: dict[str, Any]) -> int:
    config = input_payload.get("generation_config")
    if config is None:
        return DEFAULT_MAX_RESEARCH_LEADS
    return config["max_research_leads"]


def finalize(input_payload: Any, draft_payload: Any) -> dict[str, Any]:
    _validate_schema(INPUT_VALIDATOR, input_payload, "input")
    input_payload = cast(dict[str, Any], input_payload)
    draft = validate_draft(draft_payload)

    edges, findings = _build_indexes(input_payload)
    validated_leads: list[dict[str, Any]] = []
    for index, lead in enumerate(draft["research_leads"]):
        validated_leads.append(finalize_lead(lead))

    unique_leads = validated_leads
    duplicate_count = 0
    limit = _max_research_leads(input_payload)
    selected_leads = [
        {**lead, "local_lead_key": f"lead-{index}"}
        for index, lead in enumerate(unique_leads, start=1)
    ]

    degraded = not bool(selected_leads)
    warnings = copy.deepcopy(draft["generation_warnings"])

    result = {
        "research_leads": selected_leads,
        "summary": {
            "input_finding_count": len(findings),
            "input_gap_edge_count": sum(1 for edge in edges.values() if edge["edge_status"] == "gap"),
            "research_lead_count": len(selected_leads),
            "dropped_duplicate_lead_count": duplicate_count,
            "dropped_over_limit_lead_count": max(0, len(unique_leads) - len(selected_leads)),
            "max_research_leads": limit,
            "degraded": degraded,
        },
        "audit": {
            "skill_id": SKILL_ID,
            "task_id": input_payload["task_id"],
            "traceability_mode": "upstream_source_binding",
            "guard_version": GUARD_VERSION,
            "input_fingerprint": build_input_fingerprint(input_payload),
            "claim_layer": "research_lead",
            "novelty_boundary": NOVELTY_BOUNDARY,
        },
        "generation_warnings": warnings,
    }
    _validate_schema(OUTPUT_VALIDATOR, result, "final output")
    return result

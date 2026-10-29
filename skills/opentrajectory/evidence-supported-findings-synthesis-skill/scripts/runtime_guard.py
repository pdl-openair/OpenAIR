from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft7Validator

from scripts.identity import build_finding_id, build_input_fingerprint

GUARD_VERSION = "s1-findings-v2"
SKILL_ID = "ot_evidence_supported_findings_synthesis"
DEFAULT_MAX_FINDINGS = 12
STATUSES = ("supported", "uncertain", "conflicting", "boundary_limited")

ROOT = Path(__file__).resolve().parents[1]


class ContractRejected(ValueError):
    pass


def _load_validator(filename: str) -> Draft7Validator:
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


def validate_final_output(output_payload: Any) -> None:
    _validate_schema(OUTPUT_VALIDATOR, output_payload, "final output")


def _build_edge_index(input_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    edges: dict[str, dict[str, Any]] = {}
    mechanism_packet = input_payload.get("mechanism_packet")
    if mechanism_packet is None:
        return edges
    for edge in mechanism_packet["mechanism_edges"]:
        edge_id = edge["edge_id"]
        edges[edge_id] = edge
    return edges


def _canonicalize_references(finding: dict[str, Any]) -> dict[str, Any]:
    canonical = copy.deepcopy(finding)
    for field in (
        "provenance_evidence_ids",
        "basis_mechanism_edge_ids",
        "gap_mechanism_edge_ids",
    ):
        canonical[field] = sorted(canonical[field])
    return canonical


def finalize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    finalized = _canonicalize_references(finding)
    finalized["finding_id"] = build_finding_id(finalized)
    finalized["claim_type"] = "evidence_supported_finding"
    return finalized


def _max_findings(input_payload: dict[str, Any]) -> int:
    config = input_payload.get("generation_config")
    if config is None:
        return DEFAULT_MAX_FINDINGS
    return config.get("max_findings", DEFAULT_MAX_FINDINGS)


def finalize(input_payload: Any, draft_payload: Any) -> dict[str, Any]:
    _validate_schema(INPUT_VALIDATOR, input_payload, "input")
    input_payload = cast(dict[str, Any], input_payload)
    draft = validate_draft(draft_payload)

    edges = _build_edge_index(input_payload)
    validated_findings: list[dict[str, Any]] = []
    for index, finding in enumerate(draft["findings"]):
        validated_findings.append(finalize_finding(finding))

    unique_findings = validated_findings
    duplicate_count = 0
    limit = _max_findings(input_payload)
    selected_findings = [
        {**finding, "local_finding_key": f"finding-{index}"}
        for index, finding in enumerate(unique_findings, start=1)
    ]

    warnings = copy.deepcopy(draft["generation_warnings"])
    degraded = not bool(selected_findings)

    status_counts = {status: 0 for status in STATUSES}
    for finding in selected_findings:
        status = finding["epistemic_status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    result = {
        "findings": selected_findings,
        "summary": {
            "input_mechanism_edge_count": len(edges),
            "finding_count": len(selected_findings),
            "status_counts": status_counts,
            "dropped_duplicate_finding_count": duplicate_count,
            "dropped_over_limit_finding_count": max(0, len(unique_findings) - len(selected_findings)),
            "max_findings": limit,
            "degraded": degraded,
        },
        "audit": {
            "skill_id": SKILL_ID,
            "task_id": input_payload["task_id"],
            "traceability_mode": "upstream_source_binding",
            "guard_version": GUARD_VERSION,
            "input_fingerprint": build_input_fingerprint(input_payload),
            "claim_layer": "evidence_supported_finding",
        },
        "generation_warnings": warnings,
    }
    validate_final_output(result)
    return result

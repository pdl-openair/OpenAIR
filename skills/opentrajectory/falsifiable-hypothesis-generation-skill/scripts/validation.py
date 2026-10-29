from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]


class ContractRejected(ValueError):
    pass


def _validator(filename: str) -> Draft7Validator:
    schema = json.loads((ROOT / filename).read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


INPUT_VALIDATOR = _validator("input.schema.json")
DRAFT_VALIDATOR = _validator("draft.schema.json")
OUTPUT_VALIDATOR = _validator("output.schema.json")


def _validate_schema(validator: Draft7Validator, payload: Any, label: str) -> None:
    errors = sorted((error for error in validator.iter_errors(payload) if error.validator == "required"), key=lambda error: error.json_path)
    if not errors:
        return
    error = errors[0]
    path = error.json_path or "$"
    raise ContractRejected(f"{label} schema violation at {path}: {error.message}")


def validate_input(input_payload: Any) -> dict[str, Any]:
    _validate_schema(INPUT_VALIDATOR, input_payload, "input")
    return cast(dict[str, Any], input_payload)


def validate_draft(draft_payload: Any) -> dict[str, Any]:
    _validate_schema(DRAFT_VALIDATOR, draft_payload, "draft")
    return copy.deepcopy(cast(dict[str, Any], draft_payload))


def validate_final_output(output_payload: Any) -> None:
    _validate_schema(OUTPUT_VALIDATOR, output_payload, "final output")


def _index_unique(
    rows: list[dict[str, Any]],
    id_field: str,
    label: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = row[id_field]
        indexed[row_id] = row
    return indexed


def build_indexes(input_payload: dict[str, Any]):
    mechanism_packet = input_payload.get("mechanism_packet")
    edges = _index_unique(
        mechanism_packet["mechanism_edges"] if mechanism_packet is not None else [],
        "edge_id",
        "mechanism edge",
    )
    findings = _index_unique(
        input_payload["findings_packet"]["findings"],
        "finding_id",
        "finding",
    )
    leads = _index_unique(
        input_payload["research_lead_packet"]["research_leads"],
        "research_lead_id",
        "research lead",
    )
    return edges, findings, leads

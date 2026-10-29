from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[1]
SKILL_ID = "ot_v3_evidence_assessment_synthesis"
GUARD_VERSION = "ot-v3-evidence-assessment-v1"


class ContractRejected(ValueError):
    pass


def _validator(name: str) -> Draft7Validator:
    return Draft7Validator(json.loads((ROOT / name).read_text(encoding="utf-8")))


INPUT = _validator("input.schema.json")
DRAFT = _validator("draft.schema.json")
OUTPUT = _validator("output.schema.json")


def _validate(validator: Draft7Validator, payload: Any, label: str) -> None:
    errors = sorted((error for error in validator.iter_errors(payload) if error.validator == "required"), key=lambda error: str(error.path))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.path) or "<root>"
        raise ContractRejected(f"{label} schema violation at {path}: {error.message}")


def finalize(input_payload: Any, draft_payload: Any) -> dict[str, Any]:
    _validate(INPUT, input_payload, "input")
    _validate(DRAFT, draft_payload, "draft")
    input_payload = copy.deepcopy(input_payload)
    assessment = copy.deepcopy(draft_payload)
    record_ids = {str(row["record_id"]) for row in input_payload["research_records"]}
    result = {
        "evidence_assessment_and_research_synthesis": assessment,
        "summary": {"input_research_record_count": len(record_ids), "judgment_count": len(assessment["evidence_judgments"]), "conflict_count": len(assessment["conflicts"]), "gap_count": len(assessment["evidence_gaps"]), "degraded": not bool(assessment["evidence_judgments"])},
        "audit": {"skill_id": SKILL_ID, "task_id": input_payload["task_id"], "traceability_mode": "research_record_source_binding", "guard_version": GUARD_VERSION},
        "synthesis_warnings": assessment["synthesis_warnings"],
    }
    _validate(OUTPUT, result, "output")
    return result

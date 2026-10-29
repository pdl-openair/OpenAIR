"""Deterministic finalization for PVD EvidenceSchema design."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()


def _failed(code: str) -> FinalizationResult:
    return FinalizationResult("failed", None, (code,))


def _identities(input_payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    structured = input_payload.get("structured_context")
    parameter_set = input_payload.get("recommendation_parameter_set")
    judgments = input_payload.get("parameter_admission_judgments")
    if not isinstance(structured, Mapping):
        raise ValueError("finalize_missing_input_structured_context")
    if not isinstance(parameter_set, Mapping) or not isinstance(parameter_set.get("parameters"), list):
        raise ValueError("finalize_missing_input_recommendation_parameter_set")
    if not isinstance(judgments, list):
        raise ValueError("finalize_missing_input_parameter_admission_judgments")
    params = parameter_set["parameters"]
    formal = [str(row.get("parameter_key") or "").strip() for row in params if isinstance(row, Mapping)]
    pending = [str(row.get("parameter_key") or "").strip() for row in judgments if isinstance(row, Mapping)]
    return formal, pending


def _finalize_body(input_payload: dict[str, Any], draft_payload: dict[str, Any]) -> dict[str, Any]:
    formal, pending = _identities(input_payload)
    allowed = formal + pending
    rows = draft_payload.get("evidence_targets")
    covered: set[str] = set()
    targets: list[dict[str, Any]] = []
    for index, raw in enumerate(rows, start=1):
        row = deepcopy(dict(raw))
        row_keys = row.get("covered_parameter_keys")
        covered.update(row_keys)
        row["target_id"] = f"target_{index:03d}"
        targets.append(row)
    coverage = {
        "formal_parameter_keys": formal,
        "pending_parameter_keys": pending,
        "all_parameter_keys": allowed,
    }
    return {
        "evidence_schema": {
            "contract": "pvd-evidence-schema/v1",
            "evidence_targets": targets,
            "gap_taxonomy": list(draft_payload.get("gap_taxonomy") or []),
            "uncertainty_rules": list(draft_payload.get("uncertainty_rules") or []),
        },
        "coverage_target": coverage,
        "pending_parameter_evidence_identities": pending,
        "design_warnings": list(draft_payload.get("design_warnings") or []),
    }


def finalize(input_payload: dict[str, Any], draft_payload: dict[str, Any]) -> FinalizationResult:
    try:
        return FinalizationResult("succeeded", _finalize_body(input_payload, draft_payload))
    except ValueError as exc:
        return _failed(str(exc))

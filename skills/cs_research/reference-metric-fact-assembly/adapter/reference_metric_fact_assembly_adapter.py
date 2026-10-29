"""Deterministically assemble source-grounded reference metrics for comparison."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

_SCHEMA_VERSION = "openair-reference-metrics/v1"
def _unresolved(target: str, reason: str) -> dict[str, str]:
    return {"target": target or "reference_metric", "reason": reason}


def _find_exact_excerpt(content: str, excerpt: Any) -> tuple[int, int] | None:
    if not isinstance(excerpt, str) or not excerpt:
        return None
    first = content.find(excerpt)
    if first < 0:
        return None
    # Ambiguous repeated excerpts are not sufficient for deterministic provenance.
    if content.find(excerpt, first + 1) >= 0:
        return None
    line_start = content.count("\n", 0, first) + 1
    line_end = line_start + excerpt.count("\n")
    return line_start, line_end


def _reference_id(candidate: Mapping[str, Any], document_id: str, source_sha256: str) -> str:
    payload = {
        "document_id": document_id,
        "source_sha256": source_sha256,
        "metric_name": candidate.get("metric_name"),
        "reference_value": candidate.get("reference_value"),
        "value_type": candidate.get("value_type"),
        "unit": candidate.get("unit"),
        "experiment_conditions": candidate.get("experiment_conditions"),
        "comparison_rule": candidate.get("comparison_rule"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"refmetric:{digest}"


def _assemble_record(
    candidate: Mapping[str, Any], *, documents: Mapping[str, Mapping[str, Any]],
    source_package: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    document_id = candidate.get("source_document_id")
    document = documents.get(str(document_id), {})
    source_lines = _find_exact_excerpt(str(document.get("content", "")), candidate.get("source_excerpt"))
    raw_rule = candidate.get("comparison_rule")
    raw_basis = candidate.get("comparison_rule_basis_excerpt")
    formal_rule = None
    if isinstance(raw_rule, Mapping):
        formal_rule = dict(raw_rule)
        basis_lines = _find_exact_excerpt(str(document.get("content", "")), raw_basis)
        formal_rule["basis"] = {
            "document_id": document_id, "path": document.get("path"),
            "line_start": basis_lines[0] if basis_lines else None,
            "line_end": basis_lines[1] if basis_lines else None,
            "excerpt": raw_basis,
        }
    return {
        "reference_id": _reference_id(candidate, str(document_id), str(document.get("sha256"))),
        **{key: candidate.get(key) for key in ("metric_name", "reference_value", "value_type", "unit", "experiment_conditions")},
        "source": {
            "source_kind": source_package.get("source_kind"), "repository": source_package.get("repository"),
            "resolved_commit_sha": source_package.get("resolved_commit_sha"), "document_id": document_id,
            "path": document.get("path"), "sha256": document.get("sha256"),
            "locator": {"line_start": source_lines[0] if source_lines else None,
                        "line_end": source_lines[1] if source_lines else None, "excerpt": candidate.get("source_excerpt")},
        },
        "comparison_rule_status": "provided" if formal_rule is not None else "missing",
        "comparison_rule": formal_rule,
    }, None


def run_reference_metric_fact_assembly(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not {"source_package", "interpretation"}.issubset(payload):
        return {"state": "failed", "failure_code": "skill_input_invalid"}
    source_package = payload.get("source_package")
    interpretation = payload.get("interpretation")
    if not isinstance(source_package, Mapping) or not isinstance(interpretation, Mapping):
        return {"state": "failed", "failure_code": "skill_input_invalid"}
    if not isinstance(source_package.get("documents"), list):
        return {"state": "failed", "failure_code": "source_package_invalid"}

    documents: dict[str, Mapping[str, Any]] = {}
    for row in source_package["documents"]:
        if not isinstance(row, Mapping):
            return {"state": "failed", "failure_code": "source_package_invalid"}
        document_id = row.get("document_id")
        if not isinstance(document_id, str) or document_id in documents:
            return {"state": "failed", "failure_code": "source_package_invalid"}
        documents[document_id] = row

    raw_candidates = interpretation.get("reference_candidates")
    raw_unresolved = interpretation.get("unresolved")
    if not isinstance(raw_candidates, list) or not isinstance(raw_unresolved, list):
        return {"state": "failed", "failure_code": "interpretation_invalid"}

    unresolved: list[dict[str, str]] = []
    for row in raw_unresolved:
        if isinstance(row, Mapping) and isinstance(row.get("target"), str) and isinstance(row.get("reason"), str):
            unresolved.append({"target": row["target"], "reason": row["reason"]})

    records: list[dict[str, Any]] = []
    for index, candidate in enumerate(raw_candidates):
        if not isinstance(candidate, Mapping):
            unresolved.append(_unresolved(f"candidate[{index}]", "candidate_invalid"))
            continue
        record, error = _assemble_record(candidate, documents=documents, source_package=source_package)
        if record is None:
            unresolved.append(_unresolved(str(candidate.get("metric_name") or f"candidate[{index}]"), error or "candidate_invalid"))
            continue
        records.append(record)
        if record["comparison_rule"] is None:
            unresolved.append(_unresolved(record["metric_name"], "comparison_rule_basis_missing"))

    requests = [
        {**{key: record[key] for key in ("metric_name", "reference_value", "value_type", "unit")}, "comparison_rule": {key: value for key, value in record["comparison_rule"].items() if key != "basis"}}
        for record in records if record["comparison_rule"] is not None
    ]
    conflicts: list[dict[str, Any]] = []

    status = "complete" if records and requests and not conflicts and not unresolved else "incomplete"
    return {
        "schema_version": _SCHEMA_VERSION,
        "assembly_status": status,
        "reference_metric_records": records,
        "reference_metric_comparison_requests": requests,
        "reference_metric_source_conflicts": conflicts,
        "reference_metric_unresolved": unresolved,
    }


__all__ = ["run_reference_metric_fact_assembly"]

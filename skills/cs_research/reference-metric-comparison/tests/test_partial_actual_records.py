"""Partial actual records stay comparable item by item."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))
CONDITION_KEYS = ("task_scope", "implementation", "data_split", "preprocessing", "evaluation_protocol", "metric_definition", "randomness", "hardware_environment")
from adapter.reference_metric_comparison_adapter import (  # noqa: E402
    run_reference_metric_comparison,
)

OUTPUT_SCHEMA = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
TRACE = {"source_sha256": "a" * 64, "column": "audit_metric"}
NONDECISIVE_UNKNOWN = "confidence interval is unavailable"


def _actual(
    name: str,
    value,
    *,
    value_type: str = "number",
    unit=None,
    status: str = "succeeded",
    **extra,
) -> dict:
    row = {
        "metric_name": name,
        "value_type": value_type,
        "unit": unit,
        "extraction_status": status,
    }
    if "actual_value" in extra:
        row["actual_value"] = extra.pop("actual_value")
    elif status == "succeeded":
        row["actual_value"] = value
    else:
        row["actual_value"] = None
        row["failure_code"] = "metric_not_found"
    if status == "succeeded" and "source_trace" not in extra:
        row["source_trace"] = {"source_sha256": "a" * 64, "column": name}
    row.update(extra)
    return row


def _reference(
    name: str,
    value,
    *,
    value_type: str = "number",
    unit=None,
    rule: dict | None = None,
) -> dict:
    return {
        "metric_name": name,
        "reference_value": value,
        "value_type": value_type,
        "unit": unit,
        "comparison_rule": rule or {"kind": "exact"},
    }


def _condition(status: str = "compatible", refs: list[str] | None = None) -> dict:
    return {
        "status": status,
        "evidence_refs": ["evidence-condition"] if refs is None else refs,
    }


def _assessment(**overrides) -> dict:
    assessment = {
        "status": "comparable",
        "conditions": {key: _condition() for key in CONDITION_KEYS},
        "condition_mismatches": [],
        "evidence_refs": ["evidence-top"],
        "unknowns": [],
    }
    assessment.update(overrides)
    return assessment


def _records(rows: list, **overrides) -> dict:
    records = {
        "status": "complete",
        "execution_record_ref": "execution/audit/execution-record.json",
        "metrics": rows,
        "reference_values_used": False,
        "partial_failures": [],
        "unknowns": [],
    }
    records.update(overrides)
    return records


def _compare(
    records: dict,
    references: list[dict],
    assessment: dict | None = None,
) -> dict:
    payload = {
        "actual_metric_records": records,
        "reference_metrics": references,
        "reproduction_comparability_assessment": (
            _assessment() if assessment is None else assessment
        ),
    }
    result = run_reference_metric_comparison(payload)
    jsonschema.validate(result, OUTPUT_SCHEMA)
    return result


def test_nondecisive_unknown_keeps_the_same_legal_metric_difference() -> None:
    references = [_reference("audit_metric", 1.0)]
    clean = _records([_actual("audit_metric", 1.0)])
    dirty = _records(
        [_actual("audit_metric", 1.0)],
        unknowns=[NONDECISIVE_UNKNOWN, "not_comparable", "mismatch"],
        partial_failures=[{"metric_name": "unused", "failure_code": "not_requested"}],
    )
    frozen = json.dumps(dirty, sort_keys=True)

    positive = _compare(clean, references)
    candidate = _compare(dirty, references)

    assert positive["overall_result"] == "meets_standard"
    assert positive["compared_metric_count"] == 1
    assert candidate["state"] == "succeeded"
    assert candidate["comparison_status"] == "complete"
    assert candidate["overall_result"] == "meets_standard"
    assert candidate["compared_metric_count"] == 1
    assert (
        candidate["metrics"][0]["calculation_trace"] == positive["metrics"][0]["calculation_trace"]
    )
    assert candidate["metrics"][0]["calculation_trace"]["signed_delta_decimal"] == "0"
    assert json.dumps(dirty, sort_keys=True) == frozen

    short = _records([_actual("audit_metric", 0.5)], unknowns=[NONDECISIVE_UNKNOWN])
    missed = _compare(short, references)
    assert missed["overall_result"] == "does_not_meet_standard"
    assert missed["metrics"][0]["meets_rule"] is False
    assert missed["metrics"][0]["calculation_trace"]["signed_delta_decimal"] == "-0.5"
    assert short["unknowns"] == [NONDECISIVE_UNKNOWN]


def test_condition_note_text_does_not_decide_comparability() -> None:
    assessment = _assessment(unknowns=["the word mismatch appears in this note only"])
    assessment["conditions"]["randomness"] = {
        "status": "compatible",
        "evidence_refs": ["evidence-randomness"],
        "note": "status stays compatible even though the note says unknown",
    }
    result = _compare(
        _records([_actual("audit_metric", 1.0)]),
        [_reference("audit_metric", 1.0)],
        assessment,
    )
    assert result["overall_result"] == "meets_standard"
    assert result["compared_metric_count"] == 1


def test_generic_sdr_actual_metrics_semantics_stay_unchanged() -> None:
    def document(rows: list[dict]) -> dict:
        complete = all(row["extraction_status"] == "succeeded" for row in rows)
        return {
            "schema_version": "openair-actual-metrics/v1",
            "state": "succeeded" if complete else "failed",
            "extraction_status": "complete" if complete else "incomplete",
            "metrics": rows,
        }

    exact = run_reference_metric_comparison(
        {
            "actual_metrics": document([_actual("score", 81.58)]),
            "reference_metrics": [_reference("score", 81.58)],
        }
    )
    jsonschema.validate(exact, OUTPUT_SCHEMA)
    assert exact["state"] == "succeeded"
    assert exact["comparison_status"] == "complete"
    assert exact["overall_result"] == "meets_standard"
    assert exact["compared_metric_count"] == 1
    assert exact["metrics"][0]["calculation_trace"]["signed_delta_decimal"] == "0"

    partial = run_reference_metric_comparison(
        {
            "actual_metrics": document([_actual("ok", 1.0), _actual("bad", None, status="failed")]),
            "reference_metrics": [_reference("ok", 1.0), _reference("bad", 1.0)],
        }
    )
    jsonschema.validate(partial, OUTPUT_SCHEMA)
    assert partial["state"] == "failed"
    assert partial["comparison_status"] == "incomplete"
    assert partial["overall_result"] == "indeterminate"
    assert partial["compared_metric_count"] == 1
    assert partial["failure_code"] == "comparison_incomplete"
    assert partial["metrics"][0]["meets_rule"] is True
    assert partial["metrics"][1]["failure_code"] == "actual_metric_unavailable"


def test_semantic_notes_and_units_do_not_gate_numeric_comparison():
    result = _compare(_records([_actual("metric", "1.0", unit="另一单位", source_trace={})], reference_values_used=True), [_reference("metric", 1.0, unit="原单位")], _assessment(status="not_comparable"))
    assert result["overall_result"] == "meets_standard"
    assert result["metrics"][0]["actual"]["value"] == "1.0"


def test_all_same_name_candidates_are_calculated_without_arbitrary_selection():
    result = _compare(_records([_actual("metric", 1), _actual("metric", 2)]), [_reference("metric", 1), _reference("metric", 2)])
    assert result["compared_metric_count"] == 4
    assert [(row["actual"]["value"], row["reference"]["value"]) for row in result["metrics"]] == [(1,1),(2,1),(1,2),(2,2)]


def test_failed_calculation_retains_raw_operands_and_other_success():
    result = _compare(_records([_actual("ok", 1), _actual("bad", {"raw": "value"})]), [_reference("ok",1), _reference("bad",1)])
    assert result["compared_metric_count"] == 1
    assert result["metrics"][1]["failure_code"] == "comparison_calculation_unavailable"
    assert result["metrics"][1]["actual"]["value"] == {"raw":"value"}

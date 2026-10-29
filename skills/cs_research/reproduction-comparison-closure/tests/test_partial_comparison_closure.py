"""Closure keeps formal comparisons and hands unknowns to the conclusion consumer."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sys

import jsonschema

CS_ROOT = Path(__file__).resolve().parents[2]
COMPARISON_ROOT = CS_ROOT / "reference-metric-comparison"
CLOSURE_ROOT = Path(__file__).resolve().parents[1]
CONCLUSION_ROOT = CS_ROOT / "reproduction-conclusion-closure"
CLOSURE_SCHEMA = json.loads((CLOSURE_ROOT / "output.schema.json").read_text(encoding="utf-8"))


def _load_package(name: str, skill_root: Path):
    init = skill_root / "adapter" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name,
        init,
        submodule_search_locations=[str(skill_root / "adapter")],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_package("cs_comp_reference_adapter", COMPARISON_ROOT)
_load_package("cs_comp_closure_adapter", CLOSURE_ROOT)
comparison = importlib.import_module(
    "cs_comp_reference_adapter.reference_metric_comparison_adapter"
)
contract = importlib.import_module("cs_comp_reference_adapter.comparison_contract")
closure = importlib.import_module("cs_comp_closure_adapter.reproduction_comparison_closure_adapter")

_conclusion_spec = importlib.util.spec_from_file_location(
    "cs_comp_conclusion_adapter",
    CONCLUSION_ROOT / "adapter" / "reproduction_conclusion_closure_adapter.py",
)
assert _conclusion_spec is not None and _conclusion_spec.loader is not None
conclusion = importlib.util.module_from_spec(_conclusion_spec)
_conclusion_spec.loader.exec_module(conclusion)

CONDITION_KEYS = contract.CONDITION_KEYS
NONDECISIVE_UNKNOWN = "confidence interval is unavailable"


def _actual(name: str, value, *, status: str = "succeeded") -> dict:
    row = {
        "metric_name": name,
        "actual_value": value if status == "succeeded" else None,
        "value_type": "number",
        "unit": None,
        "extraction_status": status,
    }
    if status == "succeeded":
        row["source_trace"] = {"source_sha256": "b" * 64, "column": name}
    else:
        row["failure_code"] = "metric_not_found"
    return row


def _reference(name: str, value) -> dict:
    return {
        "metric_name": name,
        "reference_value": value,
        "value_type": "number",
        "unit": None,
        "comparison_rule": {"kind": "exact"},
    }


def _condition(status: str = "compatible", refs: list[str] | None = None) -> dict:
    return {"status": status, "evidence_refs": ["evidence-condition"] if refs is None else refs}


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


def _close(records: dict, references: list[dict], assessment: dict) -> tuple[dict, dict]:
    compared = comparison.run_reference_metric_comparison({
        "actual_metric_records": records,
        "reference_metrics": references,
        "reproduction_comparability_assessment": assessment,
    })
    closed = closure.run_reproduction_comparison_closure({
        "reproduction_comparability_assessment": assessment,
        "deterministic_comparison": compared,
    })
    jsonschema.Draft202012Validator(CLOSURE_SCHEMA).validate(closed)
    return compared, closed


def _conclude(records: dict, closed: dict) -> dict:
    return conclusion.run_reproduction_conclusion_closure({
        "reproduction_execution_record": {"status": "partial", "unknowns": []},
        "actual_metric_records": records,
        "reproduction_comparison_judgment": closed,
    })


def test_top_level_unknown_stays_readable_and_does_not_overturn_a_formal_pass() -> None:
    records = _records(
        [_actual("audit_metric", 1.0)],
        unknowns=[NONDECISIVE_UNKNOWN],
    )
    assessment = _assessment(unknowns=["reference interval is not part of the declared rule"])
    compared, closed = _close(
        records,
        [_reference("audit_metric", 1.0)],
        assessment,
    )
    concluded = _conclude(records, closed)

    assert compared["overall_result"] == "meets_standard"
    assert compared["metrics"][0]["calculation_trace"]["signed_delta_decimal"] == "0"
    assert closed["overall_verdict"] == "meets_standard"
    assert closed["unknowns"] == ["reference interval is not part of the declared rule"]
    assert closed["metric_judgments"][0]["meets_rule"] is True
    assert assessment["unknowns"] == ["reference interval is not part of the declared rule"]
    assert records["unknowns"] == [NONDECISIVE_UNKNOWN]
    assert concluded["verdict"] == "reproduced"
    handed = {
        (item["source"], item["text"])
        for item in concluded["upstream_unknowns"]
    }
    assert ("actual_metric_records", NONDECISIVE_UNKNOWN) in handed
    assert ("reproduction_comparison_judgment", "reference interval is not part of the declared rule") in handed


def test_partial_metric_failure_stays_indeterminate_without_dropping_the_success() -> None:
    records = _records(
        [_actual("ok", 3.0), _actual("bad", None, status="failed")],
        status="incomplete",
        unknowns=["bad metric was not extracted"],
        partial_failures=[{"metric_name": "bad"}],
    )
    assessment = _assessment()
    compared, closed = _close(
        records,
        [_reference("ok", 3.0), _reference("bad", 1.0)],
        assessment,
    )
    concluded = _conclude(records, closed)

    assert compared["overall_result"] == "indeterminate"
    assert compared["compared_metric_count"] == 1
    assert closed["overall_verdict"] == "indeterminate"
    assert closed["overall_verdict"] != "meets_standard"
    assert closed["unknowns"] == ["comparison_incomplete"]
    judgments = {row["metric_name"]: row for row in closed["metric_judgments"]}
    assert judgments["ok"]["meets_rule"] is True
    assert judgments["ok"]["calculation_trace"]["signed_delta_decimal"] == "0"
    assert judgments["bad"]["failure_code"] == "actual_metric_unavailable"
    assert concluded["verdict"] == "indeterminate"
    assert concluded["comparison"]["metric_judgments"][0]["metric_name"] == "ok"
    assert ("actual_metric_records", "bad metric was not extracted") in {
        (item["source"], item["text"]) for item in concluded["upstream_unknowns"]
    }


def test_decisive_condition_gaps_cannot_close_as_success_and_remain_readable() -> None:
    records = _records([_actual("audit_metric", 1.0)])
    references = [_reference("audit_metric", 1.0)]

    unknown_condition = _assessment()
    unknown_condition["conditions"]["evaluation_protocol"] = _condition("unknown", [])
    mismatch = _assessment(
        condition_mismatches=[
            {"condition": "data_split", "detail": "split differs"}
        ]
    )
    mismatch["conditions"]["data_split"] = _condition("mismatch")
    no_evidence = _assessment(evidence_refs=[])
    indeterminate = _assessment(status="indeterminate")

    for assessment in (unknown_condition, mismatch, no_evidence, indeterminate):
        _compared, closed = _close(records, references, assessment)
        concluded = _conclude(records, closed)
        assert closed["overall_verdict"] == "indeterminate"
        assert concluded["verdict"] == "indeterminate"
        assert "comparability_not_established" in closed["unknowns"]

    unknown_closed = _close(records, references, unknown_condition)[1]
    assert (
        unknown_closed["comparability"]["conditions"]["evaluation_protocol"]["status"]
        == "unknown"
    )
    mismatch_closed = _close(records, references, mismatch)[1]
    assert mismatch_closed["condition_mismatches"] == [
        {"condition": "data_split", "detail": "split differs"}
    ]
    assert mismatch_closed["comparability"]["conditions"]["data_split"]["status"] == "mismatch"


def test_borrowed_reference_values_do_not_reach_a_reproduction_success() -> None:
    records = _records([_actual("audit_metric", 1.0)], reference_values_used=True)
    assessment = _assessment()
    _compared, closed = _close(records, [_reference("audit_metric", 1.0)], assessment)
    concluded = _conclude(records, closed)
    assert closed["overall_verdict"] == "indeterminate"
    assert closed["metric_judgments"] == []
    assert closed["rule_application"]["failure_code"] == "skill_input_invalid"
    assert "skill_input_invalid" in closed["unknowns"]
    assert concluded["verdict"] == "indeterminate"

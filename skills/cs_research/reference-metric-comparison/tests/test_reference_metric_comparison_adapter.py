from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))
from adapter import reference_metric_comparison_adapter as adapter  # noqa: E402


def _actual_metric(name: str, value, value_type: str = "number", unit=None, *, status: str = "succeeded") -> dict:
    row = {
        "metric_name": name,
        "actual_value": value if status == "succeeded" else None,
        "value_type": value_type,
        "unit": unit,
        "extraction_status": status,
    }
    if status == "succeeded":
        row["source_trace"] = {"source_sha256": "a" * 64, "column": name}
    else:
        row["failure_code"] = "metric_not_found"
    return row


def _payload(actual_rows: list[dict], references: list[dict]) -> dict:
    return {
        "actual_metrics": {
            "schema_version": "openair-actual-metrics/v1",
            "state": "succeeded" if all(row["extraction_status"] == "succeeded" for row in actual_rows) else "failed",
            "extraction_status": "complete" if all(row["extraction_status"] == "succeeded" for row in actual_rows) else "incomplete",
            "metrics": actual_rows,
        },
        "reference_metrics": references,
    }


def _reference(name: str, value, *, value_type: str = "number", unit=None, rule: dict | None = None) -> dict:
    return {
        "metric_name": name,
        "reference_value": value,
        "value_type": value_type,
        "unit": unit,
        "comparison_rule": rule or {"kind": "exact"},
    }


def _single(actual, reference) -> dict:
    return adapter.run_reference_metric_comparison(_payload([actual], [reference]))


def test_exact_number_equality_and_difference() -> None:
    passed = _single(_actual_metric("score", 81.58), _reference("score", 81.58))
    assert passed["state"] == "succeeded"
    assert passed["overall_result"] == "meets_standard"
    assert passed["metrics"][0]["meets_rule"] is True
    assert passed["metrics"][0]["calculation_trace"]["signed_delta_decimal"] == "0"

    failed = _single(_actual_metric("score", 81.57), _reference("score", 81.58))
    assert failed["state"] == "succeeded"
    assert failed["comparison_status"] == "complete"
    assert failed["overall_result"] == "does_not_meet_standard"
    assert failed["metrics"][0]["meets_rule"] is False


def test_exact_supports_string_and_boolean_without_numeric_coercion() -> None:
    result = adapter.run_reference_metric_comparison(_payload(
        [_actual_metric("label", "PASS", "string"), _actual_metric("flag", True, "boolean")],
        [_reference("label", "PASS", value_type="string"), _reference("flag", True, value_type="boolean")],
    ))
    assert result["overall_result"] == "meets_standard"


def test_absolute_tolerance_includes_boundary_and_rejects_above_boundary() -> None:
    boundary = _single(
        _actual_metric("loss", 1.10),
        _reference("loss", 1.00, rule={"kind": "absolute_tolerance", "tolerance": 0.10}),
    )
    assert boundary["metrics"][0]["meets_rule"] is True
    assert boundary["metrics"][0]["calculation_trace"]["allowed_delta_decimal"] == "0.1"

    outside = _single(
        _actual_metric("loss", 1.1001),
        _reference("loss", 1.00, rule={"kind": "absolute_tolerance", "tolerance": 0.10}),
    )
    assert outside["metrics"][0]["meets_rule"] is False


def test_relative_tolerance_includes_boundary_and_zero_reference_has_zero_allowance() -> None:
    boundary = _single(
        _actual_metric("score", 105.0),
        _reference("score", 100.0, rule={"kind": "relative_tolerance", "tolerance": 0.05}),
    )
    assert boundary["metrics"][0]["meets_rule"] is True
    assert boundary["metrics"][0]["calculation_trace"]["allowed_delta_decimal"] == "5"

    zero_pass = _single(
        _actual_metric("offset", 0.0),
        _reference("offset", 0.0, rule={"kind": "relative_tolerance", "tolerance": 0.5}),
    )
    assert zero_pass["metrics"][0]["meets_rule"] is True

    zero_fail = _single(
        _actual_metric("offset", 0.0001),
        _reference("offset", 0.0, rule={"kind": "relative_tolerance", "tolerance": 0.5}),
    )
    assert zero_fail["metrics"][0]["meets_rule"] is False


def test_directional_rules_include_equal_boundary() -> None:
    gte_equal = _single(_actual_metric("acc", 80), _reference("acc", 80, rule={"kind": "actual_gte_reference"}))
    assert gte_equal["metrics"][0]["meets_rule"] is True
    gte_low = _single(_actual_metric("acc", 79.99), _reference("acc", 80, rule={"kind": "actual_gte_reference"}))
    assert gte_low["metrics"][0]["meets_rule"] is False

    lte_equal = _single(_actual_metric("latency", 50), _reference("latency", 50, rule={"kind": "actual_lte_reference"}))
    assert lte_equal["metrics"][0]["meets_rule"] is True
    lte_high = _single(_actual_metric("latency", 50.01), _reference("latency", 50, rule={"kind": "actual_lte_reference"}))
    assert lte_high["metrics"][0]["meets_rule"] is False


def test_metric_identity_is_exact_and_never_fuzzy_matched() -> None:
    result = _single(_actual_metric("Accuracy", 0.9), _reference("accuracy", 0.9))
    assert result["state"] == "failed"
    assert result["metrics"][0]["failure_code"] == "actual_metric_missing"


def test_missing_and_failed_actual_metrics_fail_closed() -> None:
    missing = _single(_actual_metric("a", 1.0), _reference("b", 1.0))
    assert missing["overall_result"] == "indeterminate"
    assert missing["metrics"][0]["failure_code"] == "actual_metric_missing"

    unavailable = _single(_actual_metric("a", None, status="failed"), _reference("a", 1.0))
    assert unavailable["metrics"][0]["failure_code"] == "actual_metric_unavailable"


@pytest.mark.parametrize("raw_rule", ["exact", None, 7, True, ["exact"]])
def test_non_object_rule_fails_closed_without_exception(raw_rule) -> None:
    reference = _reference("score", 1.0)
    reference["comparison_rule"] = raw_rule
    result = _single(_actual_metric("score", 1.0), reference)
    assert result["state"] == "failed"
    assert result["comparison_status"] == "incomplete"
    assert result["overall_result"] == "indeterminate"
    assert result["metrics"][0]["failure_code"] == "comparison_rule_kind_missing"
    assert result["metrics"][0]["rule"] == {"invalid_rule": raw_rule}
    import jsonschema
    output_schema = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, output_schema)


def test_upstream_valid_partial_actual_document_preserves_trusted_comparison() -> None:
    failed_row = {
        "metric_name": "missing",
        "value_type": "number",
        "unit": None,
        "extraction_status": "failed",
        "failure_code": "metric_not_found",
    }
    actual_document = {
        "schema_version": "openair-actual-metrics/v1",
        "state": "failed",
        "extraction_status": "incomplete",
        "execution_record_relative_path": "execution/example/execution-record.json",
        "execution_record_sha256": "a" * 64,
        "result_source": None,
        "requested_metric_count": 2,
        "extracted_metric_count": 1,
        "metrics": [_actual_metric("ok", 3.0), failed_row],
    }
    upstream_schema = json.loads((SKILL_ROOT.parent / "actual-metric-extraction" / "output.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.validate(actual_document, upstream_schema)

    payload = {
        "actual_metrics": actual_document,
        "reference_metrics": [_reference("ok", 3.0), _reference("missing", 1.0)],
    }
    input_schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(payload, input_schema)
    result = adapter.run_reference_metric_comparison(payload)
    assert result["state"] == "failed"
    assert result["comparison_status"] == "incomplete"
    assert result["overall_result"] == "indeterminate"
    assert result["compared_metric_count"] == 1
    assert result["metrics"][0]["comparison_status"] == "succeeded"
    assert result["metrics"][0]["meets_rule"] is True
    assert result["metrics"][1]["failure_code"] == "actual_metric_unavailable"
    assert result["metrics"][1]["actual"]["value"] is None


def test_upstream_valid_empty_actual_metrics_becomes_missing_per_metric() -> None:
    actual_document = {
        "schema_version": "openair-actual-metrics/v1",
        "state": "failed",
        "extraction_status": "incomplete",
        "execution_record_relative_path": None,
        "execution_record_sha256": None,
        "result_source": None,
        "requested_metric_count": 1,
        "extracted_metric_count": 0,
        "metrics": [],
    }
    payload = {
        "actual_metrics": actual_document,
        "reference_metrics": [_reference("score", 1.0)],
    }
    import jsonschema
    input_schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(payload, input_schema)
    result = adapter.run_reference_metric_comparison(payload)
    assert result["failure_code"] == "comparison_incomplete"
    assert result["metrics"][0]["failure_code"] == "actual_metric_missing"


def test_partial_failure_never_becomes_false_overall_success() -> None:
    result = adapter.run_reference_metric_comparison(_payload(
        [_actual_metric("ok", 1.0), _actual_metric("bad", 2.0)],
        [_reference("ok", 1.0), _reference("missing", 1.0)],
    ))
    assert result["state"] == "failed"
    assert result["comparison_status"] == "incomplete"
    assert result["overall_result"] == "indeterminate"
    assert result["compared_metric_count"] == 1
    assert result["metrics"][0]["meets_rule"] is True
    assert result["metrics"][1]["failure_code"] == "actual_metric_missing"


def test_trace_preserves_actual_reference_rule_and_decimal_calculation() -> None:
    actual = _actual_metric("score", 81.58)
    reference = _reference("score", 81.5, rule={"kind": "absolute_tolerance", "tolerance": 0.1})
    row = _single(actual, reference)["metrics"][0]
    assert row["actual"]["value"] == 81.58
    assert row["actual"]["source_trace"]["column"] == "score"
    assert row["reference"] == {"value": 81.5, "value_type": "number", "unit": None}
    assert row["rule"] == {"kind": "absolute_tolerance", "tolerance": 0.1}
    assert row["calculation_trace"]["signed_delta_decimal"] == "0.08"
    assert row["calculation_trace"]["result"] is True


def test_structurally_invalid_payload_fails_before_comparison() -> None:
    result = adapter.run_reference_metric_comparison({"actual_metrics": {}, "reference_metrics": []})
    assert result["failure_code"] == "skill_input_invalid"
    assert result["metrics"] == []


def test_schemas_are_valid_json_and_skill_maps_to_existing_result_acceptance_substage() -> None:
    input_schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    output_schema = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.Draft7Validator.check_schema(input_schema)
    jsonschema.Draft7Validator.check_schema(output_schema)
    metadata = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))
    assert metadata["stages"] == ["execution_validation"]
    assert metadata["substages"] == ["result_acceptance"]
    assert metadata["packs"] == ["sdr"]
    assert metadata["runtime_contract"]["kind"] == "deterministic"


def test_production_code_has_no_case_specific_hardcoding() -> None:
    production = "\n".join(path.read_text(encoding="utf-8") for path in (SKILL_ROOT / "adapter").glob("*.py")).lower()
    for forbidden in ["simcse", "senteval", "princeton", "sts12", "stsbenchmark", "sickrelatedness"]:
        assert forbidden not in production


def test_relative_tolerance_uses_absolute_reference_magnitude_for_negative_reference() -> None:
    result = _single(
        _actual_metric("score", -105.0),
        _reference("score", -100.0, rule={"kind": "relative_tolerance", "tolerance": 0.05}),
    )
    assert result["metrics"][0]["meets_rule"] is True
    assert result["metrics"][0]["calculation_trace"]["reference_magnitude_decimal"] == "100"


def test_large_integer_comparison_does_not_overflow_or_coerce_to_float() -> None:
    huge = 10 ** 400
    result = _single(
        _actual_metric("count", huge, "integer"),
        _reference("count", huge, value_type="integer", rule={"kind": "actual_gte_reference"}),
    )
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["meets_rule"] is True


def test_output_conforms_to_declared_schema() -> None:
    import jsonschema

    schema = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    result = _single(
        _actual_metric("score", 1.01),
        _reference("score", 1.0, rule={"kind": "absolute_tolerance", "tolerance": 0.02}),
    )
    jsonschema.validate(result, schema)

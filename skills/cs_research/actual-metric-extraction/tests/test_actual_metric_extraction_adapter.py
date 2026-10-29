from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest
import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))
from adapter import actual_metric_extraction_adapter as adapter  # noqa: E402


def _execution_record(stdout: str, *, succeeded: bool = True, truncated: bool = False) -> dict:
    return {
        "schema_version": "openair-execution-record/v1",
        "state": "succeeded" if succeeded else "failed",
        "execution_status": "succeeded" if succeeded else "non_zero_exit",
        "exit_code": 0 if succeeded else 2,
        "timed_out": False,
        "stdout": {
            "text": stdout,
            "total_bytes": len(stdout.encode()),
            "captured_bytes": len(stdout.encode()),
            "truncated": truncated,
            "stream_complete": True,
        },
        "stderr": {
            "text": "warning: unrelated 999.9\n",
            "total_bytes": 25,
            "captured_bytes": 25,
            "truncated": False,
            "stream_complete": True,
        },
    }


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: dict) -> str:
    execution_rel = "execution/exec-test/execution-record.json"
    path = tmp_path / execution_rel
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return execution_rel


def _pipe_request(execution_rel: str, metrics: list[dict] | None = None, *, table: dict | None = None) -> dict:
    source = {
        "kind": "execution_stream",
        "stream": "stdout",
        "format": "pipe_table",
    }
    if table is not None:
        source["table"] = table
    return {
        "execution_record_relative_path": execution_rel,
        "result_source": source,
        "metrics": metrics or [
            {"metric_name": "MetricA", "value_type": "number"},
            {"metric_name": "Avg.", "value_type": "number"},
        ],
    }


def test_minimal_table_contract_extracts_multiple_metrics_with_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = """heading
+---+---+---+
| MetricA | MetricB | Avg. |
+---+---+---+
| 12.50 | 9.0 | 10.75 |
+---+---+---+
"""
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(stdout))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel))
    assert result["state"] == "succeeded"
    assert result["extraction_status"] == "complete"
    assert [item["actual_value"] for item in result["metrics"]] == [12.5, 10.75]
    trace = result["metrics"][0]["source_trace"]
    assert trace["execution_record_relative_path"] == execution_rel
    assert trace["stream"] == "stdout"
    assert trace["header_line"] == 3
    assert trace["value_line"] == 5
    assert trace["column"] == "MetricA"
    assert trace["raw_value"] == "12.50"
    assert trace["table_selection"] == "requested_columns_unique_table"
    assert trace["row_selection"] == "single_data_row"


def test_only_ambiguous_table_needs_explicit_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = """+---+---+
| A | Avg. |
+---+---+
| 1 | 1.0 |
+---+---+
+---+---+
| B | Avg. |
+---+---+
| 2 | 2.0 |
+---+---+
"""
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(stdout))
    request = _pipe_request(execution_rel, [{"metric_name": "Avg.", "value_type": "number"}])
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "failed"
    assert result["failure_code"] == "table_ambiguous"

    request["result_source"]["table"] = {"header": ["A", "Avg."]}
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["actual_value"] == 1.0
    assert result["metrics"][0]["source_trace"]["table_selection"] == "explicit_header"


def test_multiple_rows_require_explicit_row_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "| MetricA | Avg. |\n| 1.0 | 1.0 |\n| 2.0 | 2.0 |\n"
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(stdout))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel))
    assert result["state"] == "failed"
    assert result["failure_code"] == "table_row_ambiguous"

    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel, table={"row_index": 1}))
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["actual_value"] == 2.0
    assert result["metrics"][0]["source_trace"]["row_selection"] == "explicit_index"


def test_column_selector_only_needed_when_metric_name_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "| score_raw | count |\n| 0.91 | 7 |\n"
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(stdout))
    request = _pipe_request(execution_rel, [
        {"metric_name": "accuracy", "selector": {"column": "score_raw"}, "value_type": "number"},
        {"metric_name": "count", "value_type": "integer"},
    ])
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "succeeded"
    assert [item["actual_value"] for item in result["metrics"]] == [0.91, 7]


def test_missing_metric_with_declared_table_fails_whole_extraction_without_hiding_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "| MetricA | MetricB | Avg. |\n| 1.0 | 2.0 | 1.5 |\n"
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(stdout))
    request = _pipe_request(
        execution_rel,
        [
            {"metric_name": "MetricA", "value_type": "number"},
            {"metric_name": "Missing", "value_type": "number"},
        ],
        table={"header": ["MetricA", "MetricB", "Avg."]},
    )
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "failed"
    assert result["failure_code"] == "metric_extraction_incomplete"
    assert result["extracted_metric_count"] == 1
    assert result["metrics"][0]["extraction_status"] == "succeeded"
    assert result["metrics"][1]["failure_code"] == "metric_not_found"


def test_malformed_source_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("not a table\n"))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel))
    assert result["state"] == "failed"
    assert result["failure_code"] == "table_not_found"


def test_duplicate_matching_table_is_ambiguous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    table = "| MetricA | MetricB | Avg. |\n| 1 | 2 | 1.5 |\n"
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(table + table))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel))
    assert result["state"] == "failed"
    assert result["failure_code"] == "table_ambiguous"


def test_non_numeric_value_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "| MetricA | Avg. |\n| N/A | 1.5 |\n"
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(stdout))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel, [
        {"metric_name": "MetricA", "value_type": "number"}
    ]))
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["actual_value"] == "N/A"


def test_failed_execution_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("| MetricA | Avg. |\n| 1 | 1.5 |\n", succeeded=False))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel))
    assert result["state"] == "failed"
    assert result["failure_code"] == "execution_not_succeeded"
    assert result["extracted_metric_count"] == 0


def test_available_truncated_stream_metrics_are_retained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("| MetricA | Avg. |\n| 1 | 1.5 |\n", truncated=True))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel))
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["actual_value"] == 1


def test_json_structured_source_requires_exact_json_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("done\n"))
    source_path = tmp_path / "results" / "metrics.json"
    source_path.parent.mkdir()
    source_path.write_text('{"scores":{"accuracy":0.91,"count":12}}', encoding="utf-8")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    request = {
        "execution_record_relative_path": execution_rel,
        "result_source": {
            "kind": "workspace_file",
            "relative_path": "results/metrics.json",
            "expected_sha256": digest,
            "format": "json",
        },
        "metrics": [
            {"metric_name": "accuracy", "selector": {"json_pointer": "/scores/accuracy"}, "value_type": "number"},
            {"metric_name": "count", "selector": {"json_pointer": "/scores/count"}, "value_type": "integer", "unit": "samples"},
        ],
    }
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "succeeded"
    assert result["metrics"][1]["unit"] == "samples"
    assert result["metrics"][1]["source_trace"]["json_pointer"] == "/scores/count"

    request["metrics"][0].pop("selector")
    assert adapter.run_actual_metric_extraction(request)["failure_code"] == "skill_input_invalid"


def test_declared_hash_does_not_gate_available_file_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("done\n"))
    source_path = tmp_path / "metrics.csv"
    source_path.write_text("MetricA,Avg.\n1,1.5\n", encoding="utf-8")
    request = {
        "execution_record_relative_path": execution_rel,
        "result_source": {
            "kind": "workspace_file",
            "relative_path": "metrics.csv",
            "expected_sha256": "0" * 64,
            "format": "csv",
        },
        "metrics": [{"metric_name": "MetricA", "value_type": "number"}],
    }
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["actual_value"] == 1


def test_runtime_created_workspace_file_gets_actual_identity_during_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("done\n"))
    source_path = tmp_path / "results" / "runtime.json"
    source_path.parent.mkdir()
    source_path.write_text('{"score":0.91}', encoding="utf-8")
    result = adapter.run_actual_metric_extraction({
        "execution_record_relative_path": execution_rel,
        "result_source": {
            "kind": "workspace_file",
            "relative_path": "results/runtime.json",
            "format": "json",
        },
        "metrics": [{
            "metric_name": "score",
            "value_type": "number",
            "selector": {"json_pointer": "/score"},
        }],
    })
    assert result["state"] == "succeeded"
    assert result["result_source"]["sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()


def test_duplicate_requested_metric_name_is_invalid_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("| MetricA | MetricB |\n| 1 | 2 |\n"))
    request = _pipe_request(execution_rel, [
        {"metric_name": "dup", "selector": {"column": "MetricA"}, "value_type": "number"},
        {"metric_name": "dup", "selector": {"column": "MetricB"}, "value_type": "number"},
    ])
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "failed"
    assert result["failure_code"] == "skill_input_invalid"


def test_csv_structured_table_uses_header_without_internal_locator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("done\n"))
    source_path = tmp_path / "results.csv"
    source_path.write_text("name,score,count\nmain,0.93,7\n", encoding="utf-8")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    request = {
        "execution_record_relative_path": execution_rel,
        "result_source": {
            "kind": "workspace_file",
            "relative_path": "results.csv",
            "expected_sha256": digest,
            "format": "csv",
        },
        "metrics": [
            {"metric_name": "score", "value_type": "number"},
            {"metric_name": "count", "value_type": "integer"},
        ],
    }
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "succeeded"
    assert [item["actual_value"] for item in result["metrics"]] == [0.93, 7]


def test_tsv_structured_table_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record("done\n"))
    source_path = tmp_path / "results.tsv"
    source_path.write_text("label\tvalue\nmain\t42.5\n", encoding="utf-8")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    request = {
        "execution_record_relative_path": execution_rel,
        "result_source": {
            "kind": "workspace_file",
            "relative_path": "results.tsv",
            "expected_sha256": digest,
            "format": "tsv",
        },
        "metrics": [{"metric_name": "value", "value_type": "number"}],
    }
    result = adapter.run_actual_metric_extraction(request)
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["actual_value"] == 42.5


def test_markdown_separator_is_not_counted_as_data_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "| MetricA | Avg. |\n|---|---|\n| 1.0 | 1.5 |\n"
    execution_rel = _workspace(tmp_path, monkeypatch, _execution_record(stdout))
    result = adapter.run_actual_metric_extraction(_pipe_request(execution_rel))
    assert result["state"] == "succeeded"
    assert result["metrics"][0]["actual_value"] == 1.0


def test_skill_is_product_mapped_to_existing_evidence_extraction_substage() -> None:
    metadata = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))
    assert metadata["stages"] == ["execution_validation"]
    assert metadata["substages"] == ["evidence_extraction"]
    assert metadata["packs"] == ["sdr"]
    assert metadata["stage"] == "execution_validation"
    assert metadata["substage"] == "evidence_extraction"
    assert metadata["runtime_contract"]["kind"] == "deterministic"


def test_production_code_has_no_case_specific_hardcoding() -> None:
    production = "\n".join(path.read_text(encoding="utf-8") for path in (SKILL_ROOT / "adapter").glob("*.py")).lower()
    for forbidden in ["simcse", "senteval", "princeton", "sts12", "stsbenchmark", "sickrelatedness"]:
        assert forbidden not in production

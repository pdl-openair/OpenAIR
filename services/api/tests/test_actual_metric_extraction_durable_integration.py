from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.execution import durable_skill_runtime


def _successful_output() -> dict:
    return {
        "schema_version": "openair-actual-metrics/v1",
        "state": "succeeded",
        "extraction_status": "complete",
        "execution_record_relative_path": "execution/20260914T000000Z/execution-record.json",
        "execution_record_sha256": "a" * 64,
        "result_source": {"kind": "execution_stream", "stream": "stdout", "format": "pipe_table", "sha256": "b" * 64},
        "requested_metric_count": 1,
        "extracted_metric_count": 1,
        "metrics": [{
            "metric_name": "accuracy",
            "actual_value": 0.91,
            "value_type": "number",
            "unit": None,
            "extraction_status": "succeeded",
            "source_trace": {
                "execution_record_relative_path": "execution/20260914T000000Z/execution-record.json",
                "source_kind": "execution_stream",
                "source_sha256": "b" * 64,
                "format": "pipe_table",
                "stream": "stdout",
                "table_index": 0,
                "row_index": 0,
                "column": "accuracy",
            },
        }],
    }


def test_durable_dispatch_uses_controlled_task_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run_python_skill_adapter(**kwargs):
        captured.update(kwargs)
        return _successful_output()

    monkeypatch.setattr(durable_skill_runtime, "prepare_task_workspace", lambda _task_id: tmp_path)
    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", fake_run_python_skill_adapter)
    result = durable_skill_runtime.execute_durable_skill({
        "taskId": "task-metric",
        "skillId": "actual_metric_extraction",
        "parameters": {
            "execution_record_relative_path": "execution/20260914T000000Z/execution-record.json",
            "result_source": {"kind": "execution_stream", "stream": "stdout", "format": "pipe_table"},
            "metrics": [{"metric_name": "accuracy", "value_type": "number"}],
        },
    })

    assert captured["execution_cwd"] == tmp_path.resolve()
    assert captured["environment_overrides"] == {"OPENAIR_ADAPTER_EXECUTION_CWD": str(tmp_path.resolve())}
    assert result["state"] == "succeeded"
    payload = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]).decode("utf-8"))
    assert payload["state"] == "succeeded"
    assert payload["metrics"][0]["actual_value"] == 0.91

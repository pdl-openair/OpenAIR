from __future__ import annotations

import base64
import json

from app.execution import durable_skill_runtime


def _actual_document() -> dict:
    return {
        "schema_version": "openair-actual-metrics/v1",
        "state": "succeeded",
        "extraction_status": "complete",
        "metrics": [{
            "metric_name": "accuracy",
            "actual_value": 0.91,
            "value_type": "number",
            "unit": None,
            "extraction_status": "succeeded",
            "source_trace": {"source_sha256": "b" * 64, "column": "accuracy"},
        }],
    }


def _references() -> list[dict]:
    return [{
        "metric_name": "accuracy",
        "reference_value": 0.90,
        "value_type": "number",
        "unit": None,
        "comparison_rule": {"kind": "actual_gte_reference"},
    }]


def test_durable_dispatch_produces_deterministic_reference_comparison_artifact() -> None:
    result = durable_skill_runtime.execute_durable_skill({
        "taskId": "task-reference",
        "skillId": "reference_metric_comparison",
        "parameters": {"actual_metrics": _actual_document(), "reference_metrics": _references()},
    })
    assert result["state"] == "succeeded"
    payload = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]).decode("utf-8"))
    assert payload["state"] == "succeeded"
    assert payload["overall_result"] == "meets_standard"
    assert payload["metrics"][0]["meets_rule"] is True

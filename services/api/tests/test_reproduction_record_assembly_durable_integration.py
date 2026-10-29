from __future__ import annotations

import base64
import json

from app.execution import durable_skill_runtime


def test_durable_dispatch_preserves_incomplete_reproduction_record() -> None:
    result = durable_skill_runtime.execute_durable_skill({
        "taskId": "task-reproduction-record",
        "skillId": "reproduction_record_assembly",
        "parameters": {
            "code_identity": None,
            "resource_identities": [],
            "environment_record": None,
            "execution_record": None,
            "actual_metrics": None,
            "reference_comparison": None,
        },
    })
    assert result["state"] == "succeeded"
    payload = json.loads(base64.b64decode(result["artifactPayload"]["contentBase64"]).decode("utf-8"))
    assert payload["state"] == "succeeded"
    assert payload["record_status"] == "incomplete"
    assert payload["reproduction_result"] == "indeterminate"
    assert payload["missing_components"] == [
        "code_identity", "environment_record", "execution_record", "actual_metrics", "reference_comparison"
    ]

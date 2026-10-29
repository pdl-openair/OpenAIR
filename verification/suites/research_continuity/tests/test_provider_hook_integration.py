"""Exercise private I/O authority through the actual durable model runner."""

import importlib.util
import json
from pathlib import Path

from app.execution import durable_skill_runtime


def test_primary_and_existing_structured_correction_both_cross_internal_io_authority(monkeypatch):
    source = Path("services/api/tests/test_durable_skill_runtime.py")
    spec = importlib.util.spec_from_file_location("existing_runtime_test_helpers", source)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    execute = durable_skill_runtime.execute_production_durable_skill
    seen = []

    def authorized_execution(request, **kwargs):
        return execute(
            {
                **request,
                "_internalProviderIoAuthorization": lambda attempt: seen.append(attempt.attempt_id),
            },
            **kwargs,
        )

    monkeypatch.setattr(
        durable_skill_runtime, "execute_production_durable_skill", authorized_execution
    )
    result, requests, evidence, timeouts = helpers._execute_generic_policy_skill(
        monkeypatch,
        response_contents=["not-json", json.dumps({"draft": "corrected draft"})],
    )
    assert result["state"] == "succeeded"
    assert len(requests) == 2
    assert seen == ["attempt-primary", "attempt-recovery"]
    assert [row["kind"] for row in evidence] == ["primary", "recovery"]
    assert all(timeout.read is None for timeout in timeouts)
    assert all(
        "_internalProviderIoAuthorization" not in row["request_body"].decode() for row in evidence
    )

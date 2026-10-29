from __future__ import annotations

import json

import pytest
from app.execution.skill_finalization_runtime import SkillFinalizationError
from test_parse_failure_delivery import (
    _assert_readable_delivery,
)
from test_parse_failure_delivery import (
    production_harness as production_harness,
)


@pytest.mark.parametrize(
    "code",
    [
        "finalizer_execution_failed",
        "finalizer_execution_timeout",
        "finalizer_result_invalid",
        "finalizer_rejected",
    ],
)
def test_local_finalization_failure_keeps_unfinalized_draft_once(
    monkeypatch, production_harness, code
):
    calls = []
    draft = {"draft": "Useful preliminary material; actual experiment unknown."}
    raw = json.dumps(draft)

    def finalize(**kwargs):
        calls.append(kwargs["draft_payload"])
        raise SkillFinalizationError(code)

    result, requests, evidence, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=[raw], finalize=finalize
    )
    protocol = _assert_readable_delivery(
        result, production_harness, raw, stage="skill_finalization_failed"
    )
    assert calls == [draft]
    assert len(requests) == 1
    assert [row["kind"] for row in evidence] == ["primary"]
    assert [row["outcome"] for row in evidence] == ["response_received"]
    assert any(f"skill_finalization_failed:{code}" in row for row in protocol["warnings"])


@pytest.mark.parametrize(
    "code",
    [
        "finalizer_code_snapshot_mismatch",
        "finalizer_skill_path_invalid",
        "finalizer_entrypoint_unavailable",
        "finalizer_code_unavailable",
    ],
)
def test_frozen_authority_failure_is_not_relabelled_as_delivery(
    monkeypatch, production_harness, code
):
    def finalize(**_kwargs):
        raise SkillFinalizationError(code)

    result, requests, evidence, _ = production_harness._execute_generic_policy_skill(
        monkeypatch, response_contents=['{"draft":"received"}'], finalize=finalize
    )
    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_finalization_runtime_failed"
    assert result["firstMismatch"] == code
    assert "artifactPayload" not in result
    assert len(requests) == 1
    assert len(evidence) == 1

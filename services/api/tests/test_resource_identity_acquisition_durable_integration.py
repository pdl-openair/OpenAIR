from __future__ import annotations

from pathlib import Path

import pytest

from app.execution import durable_skill_runtime


def _successful_output() -> dict:
    commit = "a" * 40
    return {
        "state": "succeeded",
        "resource_kind": "huggingface_file",
        "repo_id": "example/model",
        "repo_type": "model",
        "requested_revision": "v1",
        "resolved_commit_sha": commit,
        "actual_identity": {
            "kind": "huggingface_file_at_commit",
            "commit_sha": commit,
            "filename": "config.json",
            "sha256": "b" * 64,
        },
        "expected_identity_verification": "not_requested",
        "workspace_relative_path": "resource/abc/config.json",
        "execution_evidence": {
            "provider_client": "huggingface_hub/1.30.0",
            "operation": "hf_hub_download",
            "materialized": True,
        },
    }


def test_resource_success_requires_materialized_identity_evidence() -> None:
    output = _successful_output()
    assert durable_skill_runtime._has_external_adapter_output_evidence(
        "python_adapter:resource_identity_acquisition", output
    )
    output["actual_identity"]["commit_sha"] = "c" * 40
    assert not durable_skill_runtime._has_external_adapter_output_evidence(
        "python_adapter:resource_identity_acquisition", output
    )


def test_durable_dispatch_preserves_actual_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_run_python_skill_adapter(**kwargs):
        captured.update(kwargs)
        return {
            "state": "failed",
            "failure_code": "provider_unavailable",
            "failed_stage": "resolve_revision",
        }

    monkeypatch.setattr(durable_skill_runtime, "prepare_task_workspace", lambda _task_id: tmp_path)
    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", fake_run_python_skill_adapter)
    result = durable_skill_runtime.execute_durable_skill({
        "taskId": "task-resource",
        "skillId": "resource_identity_acquisition",
        "parameters": {
            "resource_kind": "huggingface_file",
            "repo_id": "example/model",
            "repo_type": "model",
            "revision": "v1",
            "filename": "config.json",
        },
    })

    assert captured["execution_cwd"] == tmp_path.resolve()
    assert captured["environment_overrides"] == {
        "OPENAIR_ADAPTER_EXECUTION_CWD": str(tmp_path.resolve())
    }
    assert result["state"] == "failed"
    assert result["failureClassification"] == "resource_identity_acquisition_failed"

def test_resource_batch_partial_failure_keeps_success_evidence() -> None:
    success = _successful_output()
    output = {
        "state": "partial_failure",
        "resources": [
            {"usage": "model", "result": success},
            {
                "usage": "dataset",
                "result": {
                    "state": "failed",
                    "failure_code": "resource_identity_mismatch",
                    "failed_stage": "verify_identity",
                },
            },
        ],
        "summary": {"total": 2, "succeeded": 1, "failed": 1},
    }
    assert durable_skill_runtime._has_external_adapter_output_evidence(
        "python_adapter:resource_identity_acquisition", output
    )
    assert durable_skill_runtime._external_adapter_evidence_outcome(
        "python_adapter:resource_identity_acquisition", output
    ) == "partial"


def test_resource_batch_rejects_summary_that_hides_failure() -> None:
    success = _successful_output()
    output = {
        "state": "succeeded",
        "resources": [
            {"usage": "model", "result": success},
            {
                "usage": "dataset",
                "result": {
                    "state": "failed",
                    "failure_code": "resource_identity_mismatch",
                    "failed_stage": "verify_identity",
                },
            },
        ],
        "summary": {"total": 2, "succeeded": 2, "failed": 0},
    }
    assert not durable_skill_runtime._has_external_adapter_output_evidence(
        "python_adapter:resource_identity_acquisition", output
    )

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution import durable_skill_runtime


def _successful_output() -> dict:
    commit = "a" * 40
    return {
        "state": "succeeded",
        "repository": "https://github.com/example/project.git",
        "requested_revision": "v0.4.0",
        "resolved_commit_sha": commit,
        "verified_head": commit,
        "workspace_relative_path": "repository",
        "execution_evidence": {
            "git_version": "git version 2.50.0",
            "operations": [
                {"operation": "clone", "exit_code": 0},
                {"operation": "resolve_revision", "exit_code": 0},
                {"operation": "checkout", "exit_code": 0},
                {"operation": "read_head", "exit_code": 0},
            ],
            "head_matches_resolved_commit": True,
        },
    }


def test_repository_success_requires_verified_git_evidence() -> None:
    output = _successful_output()
    assert durable_skill_runtime._has_external_adapter_output_evidence(
        "python_adapter:repository_revision_acquisition", output
    )
    output["verified_head"] = "b" * 40
    assert not durable_skill_runtime._has_external_adapter_output_evidence(
        "python_adapter:repository_revision_acquisition", output
    )


def test_durable_dispatch_rejects_failure_outside_frozen_formal_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_run_python_skill_adapter(**kwargs):
        captured.update(kwargs)
        return {
            "state": "failed",
            "failure_code": "revision_unresolvable",
            "failed_stage": "resolve_revision",
            "git_exit_code": 128,
        }

    monkeypatch.setattr(durable_skill_runtime, "prepare_task_workspace", lambda _task_id: tmp_path)
    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", fake_run_python_skill_adapter)
    result = durable_skill_runtime.execute_durable_skill({
        "taskId": "task-repository",
        "skillId": "repository_revision_acquisition",
        "parameters": {
            "repository": "https://github.com/example/project.git",
            "revision": "missing-revision",
        },
    })

    assert captured["execution_cwd"] == tmp_path.resolve()
    assert captured["environment_overrides"] == {
        "OPENAIR_ADAPTER_EXECUTION_CWD": str(tmp_path.resolve())
    }
    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_result_contract_invalid"

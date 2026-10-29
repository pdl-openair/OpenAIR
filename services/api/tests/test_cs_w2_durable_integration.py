from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.execution import durable_skill_runtime, python_adapter_runner
from app.modules.external_execution_checkpoint import ExternalExecutionCheckpointRepository


def _successful_output() -> dict:
    commit = "a" * 40
    return {
        "schema_version": "openair-reference-source-package/v1",
        "state": "succeeded",
        "source_kind": "official_repository",
        "repository": "https://example.test/official.git",
        "requested_revision": "main",
        "resolved_commit_sha": commit,
        "workspace_relative_path": "repository",
        "documents": [
            {
                "document_id": "refdoc:" + "b" * 64,
                "path": "README.md",
                "sha256": "c" * 64,
                "line_count": 1,
                "content": "Avg. = 81.58",
            }
        ],
        "read_evidence": {
            "git_version": "git version test",
            "verified_head": commit,
            "head_matches_upstream_artifact": True,
            "candidate_document_count": 1,
            "included_document_count": 1,
            "total_included_bytes": 12,
        },
    }


def test_official_reference_source_durable_dispatch_uses_controlled_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_run_python_skill_adapter(**kwargs):
        captured.update(kwargs)
        return _successful_output()

    monkeypatch.setattr(durable_skill_runtime, "prepare_task_workspace", lambda _task_id: tmp_path)
    monkeypatch.setattr(durable_skill_runtime, "run_python_skill_adapter", fake_run_python_skill_adapter)

    result = durable_skill_runtime.execute_durable_skill(
        {
            "taskId": "task-cs-w2-reference",
            "skillId": "official_reference_source_acquisition",
            "parameters": {
                "code_source": {
                    "state": "succeeded",
                    "repository": "https://example.test/official.git",
                    "requested_revision": "main",
                    "resolved_commit_sha": "a" * 40,
                    "verified_head": "a" * 40,
                    "workspace_relative_path": "repository",
                }
            },
        }
    )

    assert captured["adapter_id"] == "python_adapter:official_reference_source_acquisition"
    assert captured["execution_cwd"] == tmp_path.resolve()
    assert captured["environment_overrides"] == {
        "OPENAIR_ADAPTER_EXECUTION_CWD": str(tmp_path.resolve())
    }
    assert result["state"] == "succeeded"
    artifact = json.loads(
        base64.b64decode(result["artifactPayload"]["contentBase64"]).decode("utf-8")
    )
    assert artifact["state"] == "succeeded"
    assert artifact["resolved_commit_sha"] == "a" * 40


def test_official_reference_source_durable_dispatch_requires_task_workspace() -> None:
    result = durable_skill_runtime.execute_durable_skill(
        {
            "skillId": "official_reference_source_acquisition",
            "parameters": {
                "code_source": {
                    "state": "succeeded",
                    "repository": "https://example.test/official.git",
                    "requested_revision": "main",
                    "resolved_commit_sha": "a" * 40,
                    "verified_head": "a" * 40,
                    "workspace_relative_path": "repository",
                }
            },
        }
    )

    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_input_invalid"


@pytest.mark.parametrize(
    ("skill_id", "kind", "adapter_id"),
    [
        (
            "official_reference_source_acquisition",
            "deterministic",
            "python_adapter:official_reference_source_acquisition",
        ),
        (
            "pvd_evidence_applicability_comparability_analysis",
            "reasoning_adapter",
            "python_adapter:pvd_evidence_applicability_comparability_analysis",
        ),
    ],
)
def test_non_external_runtime_reaches_adapter_without_external_checkpoint_gate(
    monkeypatch, tmp_path, skill_id, kind, adapter_id
):
    calls = []

    def once(**kwargs):
        calls.append(kwargs)
        return {"state": "succeeded", "scientific_gap": "No measurement was performed."}

    def forbidden_get(*args, **kwargs):
        pytest.fail("non-external adapter entered the external checkpoint validator")

    monkeypatch.setattr(python_adapter_runner, "_run_python_skill_adapter_once", once)
    monkeypatch.setattr(ExternalExecutionCheckpointRepository, "get", forbidden_get)
    monkeypatch.setattr(durable_skill_runtime, "prepare_task_workspace", lambda _task: tmp_path)
    result = durable_skill_runtime.execute_durable_skill(
        {
            "taskId": "existing-task",
            "skillId": skill_id,
            "parameters": {"frozen_input": "preserved"},
            "capabilityCallId": "existing-call",
            "executionCheckpointId": "existing-call",
        },
        prepared_runtime={
            "skillId": skill_id,
            "skillPath": "skills/bounded",
            "runtimeKind": kind,
            "adapterId": adapter_id,
        },
    )
    assert result["state"] == "succeeded"
    assert len(calls) == 1
    assert calls[0]["input_payload"] == {"frozen_input": "preserved"}
    assert result["externalActionEvidence"] == []


@pytest.mark.parametrize(
    "checkpoint", [None, SimpleNamespace(execution_kind="direct_skill_runtime")]
)
def test_external_runtime_still_rejects_missing_or_wrong_checkpoint(monkeypatch, checkpoint):
    monkeypatch.setattr(
        ExternalExecutionCheckpointRepository, "get", lambda *args, **kwargs: checkpoint
    )
    monkeypatch.setattr(
        python_adapter_runner,
        "_run_python_skill_adapter_once",
        lambda **kwargs: pytest.fail("invalid external checkpoint executed adapter"),
    )
    result = durable_skill_runtime.execute_durable_skill(
        {
            "skillId": "materials_magnet_design_mvp",
            "parameters": {},
            "executionCheckpointId": "existing-call",
        },
        prepared_runtime={
            "skillId": "materials_magnet_design_mvp",
            "skillPath": "skills/bounded",
            "runtimeKind": "external_adapter",
            "adapterId": "python_adapter:materials_magnet_design_mvp",
        },
    )
    assert result["state"] == "unknown"
    assert result["externalResultUnknown"] is True


def test_external_runtime_recovers_staged_response_without_reexecuting_adapter(monkeypatch):
    output = {
        "state": "succeeded",
        "iterations": [
            {
                "mcp_calls": [
                    {
                        "mcp_name": "diffcsp-generate",
                        "tool": "generate_structures",
                        "request_sha256": "a" * 64,
                        "transport_outcome": "ok",
                    }
                ]
            }
        ],
        "sources": [{"doi": "10.1000/verified"}],
        "count": 1,
    }
    checkpoint = SimpleNamespace(
        execution_kind="external_adapter",
        phase="response_received",
        response_kind="adapter_result",
        response_payload=json.dumps(output).encode(),
    )
    monkeypatch.setattr(
        ExternalExecutionCheckpointRepository, "get", lambda *args, **kwargs: checkpoint
    )
    monkeypatch.setattr(
        python_adapter_runner,
        "_run_python_skill_adapter_once",
        lambda **kwargs: pytest.fail("staged external response was reexecuted"),
    )
    result = durable_skill_runtime.execute_durable_skill(
        {
            "skillId": "materials_magnet_design_mvp",
            "parameters": {},
            "executionCheckpointId": "existing-call",
        },
        prepared_runtime={
            "skillId": "materials_magnet_design_mvp",
            "skillPath": "skills/bounded",
            "runtimeKind": "external_adapter",
            "adapterId": "python_adapter:materials_magnet_design_mvp",
        },
    )
    assert result["state"] == "succeeded"
    assert result["externalActionEvidence"][0]["outcome"] == "completed"

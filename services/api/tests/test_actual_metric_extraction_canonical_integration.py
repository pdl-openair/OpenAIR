from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import venv

import pytest
import yaml

from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import PYTHON_ADAPTER_ENTRYPOINTS, resolve_python_adapter_skill
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    resolve_registered_skill_input,
    validate_registered_skill_input_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "cs_research" / "actual-metric-extraction"
EXPLORATORY_ROOT = REPO_ROOT / "skills" / "cs_research" / "exploratory-run"
PLAN_ROOT = REPO_ROOT / "skills" / "cs_research" / "execution-plan-fact-assembly"
PLAN_BINDING_ID = "bind:cs_reproduction:execution_plan_acquisition:skill:execution_plan_fact_assembly"


def _registry_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))["skills"]
    return next(row for row in rows if row["skill_id"] == "actual_metric_extraction")


def _binding_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    return next(row for row in rows if row["bindingId"] == "bind:sdr:evidence_extraction:skill:actual_metric_extraction")


def _exploratory_binding() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    return next(row for row in rows if row["bindingId"] == "bind:sdr:exploratory_execution:skill:exploratory_run")


def _plan_binding() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    return next(row for row in rows if row["bindingId"] == PLAN_BINDING_ID)


def test_registry_binding_and_adapter_use_existing_evidence_extraction_authority() -> None:
    registry = _registry_row()
    assert registry["stages"] == ["execution_validation"]
    assert registry["substages"] == ["evidence_extraction"]
    assert registry["runtime_contract"] == {
        "kind": "deterministic",
        "requires_external_action": False,
        "adapter_id": "python_adapter:actual_metric_extraction",
        "evidence_contract_version": "deterministic-output/v1",
    }
    assert resolve_python_adapter_skill("actual_metric_extraction") == (
        "actual_metric_extraction",
        "skills/cs_research/actual-metric-extraction",
    )
    assert PYTHON_ADAPTER_ENTRYPOINTS["python_adapter:actual_metric_extraction"] == (
        "adapter.actual_metric_extraction_adapter",
        "run_actual_metric_extraction",
        "payload_v1",
    )
    stage_map = yaml.safe_load((REPO_ROOT / "schemas" / "sdr" / "substage-skill-map.yaml").read_text(encoding="utf-8"))
    assert any(row["skill_id"] == "actual_metric_extraction" for row in stage_map["evidence_extraction"])

    binding = _binding_row()["inputSources"]
    fields = {row["targetField"]: row for row in binding["fields"]}
    assert set(fields) == {"execution_record_relative_path", "result_source", "metrics"}
    assert all(row["sourcePresence"] == "required" for row in fields.values())
    assert fields["execution_record_relative_path"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:sdr:exploratory_execution:skill:exploratory_run",
        "bindingVersion": 1,
        "field": "execution_record_relative_path",
    }
    assert fields["result_source"]["source"] == {
        "kind": "artifact",
        "bindingId": PLAN_BINDING_ID,
        "bindingVersion": 1,
        "field": "actual_metric_result_source",
    }
    assert fields["metrics"]["source"] == {
        "kind": "artifact",
        "bindingId": PLAN_BINDING_ID,
        "bindingVersion": 1,
        "field": "actual_metric_requests",
    }


def _snapshot() -> tuple[dict, dict]:
    target_input = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    source_output = json.loads((EXPLORATORY_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    plan_output = json.loads((PLAN_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    target_registry = _registry_row()
    target_binding = {**_binding_row(), "enabled": True}
    source_binding = {**_exploratory_binding(), "enabled": True}
    plan_binding = {**_plan_binding(), "enabled": True}
    snapshot = {
        "definitions": {
            "skills": [
                {
                    "skillId": "actual_metric_extraction",
                    "definitionVersion": 1,
                    "sourceContract": {
                        **target_registry,
                        "frozenSchemas": {"input": {"sha256": "0" * 64, "schema": target_input}},
                    },
                },
                {
                    "skillId": "exploratory_run",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {"output": {"sha256": "1" * 64, "schema": source_output}},
                    },
                },
                {
                    "skillId": "execution_plan_fact_assembly",
                    "definitionVersion": 1,
                    "sourceContract": {
                        "frozenSchemas": {"output": {"sha256": "2" * 64, "schema": plan_output}},
                    },
                },
            ],
            "agents": [],
        },
        "bindings": {"skills": [source_binding, plan_binding, target_binding]},
    }
    return snapshot, target_binding


def _artifact(execution_record_relative_path: str) -> dict:
    payload = {
        "schema_version": "openair-execution-record/v1",
        "state": "succeeded",
        "execution_status": "succeeded",
        "actual_executable": "environment/bin/python",
        "argv": ["-c", "print('metric')"],
        "working_directory": "repository",
        "environment": {
            "workspace_relative_path": "environment",
            "environment_record_relative_path": "environment/environment-record.json",
            "fingerprint_sha256": "0" * 64,
        },
        "environment_variable_names": [],
        "started_at_utc": "2026-09-14T00:00:00Z",
        "ended_at_utc": "2026-09-14T00:00:01Z",
        "duration_seconds": 1.0,
        "exit_code": 0,
        "timed_out": False,
        "stdout": {"text": "| accuracy |\n| --- |\n| 0.91 |\n", "total_bytes": 34, "captured_bytes": 34, "stream_complete": True, "truncated": False},
        "stderr": {"text": "", "total_bytes": 0, "captured_bytes": 0, "stream_complete": True, "truncated": False},
        "execution_record_relative_path": execution_record_relative_path,
    }
    return {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": "exploratory_run",
        "sourceCapabilityDefinitionVersion": 1,
        "sourceBindingRefs": [{"bindingId": "bind:sdr:exploratory_execution:skill:exploratory_run", "bindingVersion": 1}],
        "mimeType": "application/json",
        "content": json.dumps(payload).encode("utf-8"),
    }


def _plan_artifact() -> dict:
    payload = {
        "schema_version": "openair-execution-plan/v1",
        "state": "succeeded",
        "plan_status": "complete",
        "execution_plan_id": "execplan:" + "0" * 64,
        "source_identity": {},
        "resource_acquisition_requests": [],
        "python_version": "3.11",
        "requirements": ["pytest==8.0.0"],
        "verify_imports": ["pytest"],
        "dependency_install_timeout_seconds": 240,
        "find_links": [],
        "executable": "bin/python",
        "argv": ["-c", "print('metric')"],
        "working_directory": "repository",
        "timeout_seconds": 12,
        "environment_variables": {},
        "execution_resource_binding_plan": [],
        "actual_metric_result_source": {"kind": "execution_stream", "stream": "stdout", "format": "pipe_table"},
        "actual_metric_requests": [{"metric_name": "accuracy", "value_type": "number", "unit": None}],
        "metric_extraction_jobs": [],
        "execution_plan_provenance": [],
        "execution_plan_unresolved": [],
        "execution_plan_runtime_pending": [],
    }
    return {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": "execution_plan_fact_assembly",
        "sourceCapabilityDefinitionVersion": 1,
        "sourceBindingRefs": [{"bindingId": PLAN_BINDING_ID, "bindingVersion": 1}],
        "mimeType": "application/json",
        "content": json.dumps(payload).encode("utf-8"),
    }


def test_formal_input_freezes_execution_and_plan_artifacts() -> None:
    snapshot, binding = _snapshot()
    request = SkillInputRequest(task_id="task-metric", strategy="direct_skill", skill_id="actual_metric_extraction", definition_version=1)
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=request,
        input_context={
            "artifacts": [
                _artifact("execution/20260914T000000Z/execution-record.json"),
                _plan_artifact(),
            ],
        },
        skill_binding=binding,
    )
    assert resolved == {
        "execution_record_relative_path": "execution/20260914T000000Z/execution-record.json",
        "result_source": {"kind": "execution_stream", "stream": "stdout", "format": "pipe_table"},
        "metrics": [{"metric_name": "accuracy", "value_type": "number", "unit": None}],
    }
    assert validate_registered_skill_input_snapshot(
        snapshot=snapshot,
        request=request,
        resolved_input=resolved,
        skill_binding=binding,
    ) == resolved

    with pytest.raises(InputContractGateFailure) as caught:
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=request,
            input_context={"artifacts": [_plan_artifact()]},
            skill_binding=binding,
        )
    assert caught.value.code == "input_composition_source_artifact_missing"

    with pytest.raises(InputContractGateFailure) as caught:
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=request,
            input_context={"artifacts": [_artifact("execution/20260914T000000Z/execution-record.json")]},
            skill_binding=binding,
        )
    assert caught.value.code == "input_composition_source_artifact_missing"


def _python_relative() -> str:
    return "Scripts/python.exe" if os.name == "nt" else "bin/python"


def _prepare_workspace(root: Path) -> None:
    environment = root / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    record = {
        "schema_version": "openair-environment-record/v1",
        "state": "succeeded",
        "environment_identity": {"fingerprint_sha256": "0" * 64},
        "verification": {},
        "execution_evidence": {},
        "workspace_relative_path": "environment",
        "environment_record_relative_path": "environment/environment-record.json",
    }
    (environment / "environment-record.json").write_text(json.dumps(record), encoding="utf-8")
    (root / "repository").mkdir()


def test_real_exploratory_execution_flows_into_actual_metric_extraction(tmp_path: Path) -> None:
    _prepare_workspace(tmp_path)
    env = {"OPENAIR_ADAPTER_EXECUTION_CWD": str(tmp_path.resolve())}
    exploratory = run_python_skill_adapter(
        repo_root=REPO_ROOT,
        skill_id="exploratory_run",
        skill_path="skills/cs_research/exploratory-run",
        adapter_id="python_adapter:exploratory_run",
        input_payload={
            "executable": _python_relative(),
            "argv": ["-c", "print('| accuracy | loss |'); print('| --- | --- |'); print('| 0.91 | 0.2 |')"],
            "working_directory": "repository",
            "timeout_seconds": 10,
        },
        execution_cwd=tmp_path,
        environment_overrides=env,
    )
    assert exploratory["state"] == "succeeded"
    actual = run_python_skill_adapter(
        repo_root=REPO_ROOT,
        skill_id="actual_metric_extraction",
        skill_path="skills/cs_research/actual-metric-extraction",
        adapter_id="python_adapter:actual_metric_extraction",
        input_payload={
            "execution_record_relative_path": exploratory["execution_record_relative_path"],
            "result_source": {"kind": "execution_stream", "stream": "stdout", "format": "pipe_table"},
            "metrics": [
                {"metric_name": "accuracy", "value_type": "number"},
                {"metric_name": "loss", "value_type": "number"},
            ],
        },
        execution_cwd=tmp_path,
        environment_overrides=env,
    )
    assert actual["state"] == "succeeded"
    assert actual["extraction_status"] == "complete"
    values = {row["metric_name"]: row["actual_value"] for row in actual["metrics"]}
    assert values == {"accuracy": 0.91, "loss": 0.2}
    assert actual["execution_record_relative_path"] == exploratory["execution_record_relative_path"]

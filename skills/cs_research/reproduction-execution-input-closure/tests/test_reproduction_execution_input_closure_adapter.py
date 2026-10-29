from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft7Validator

SKILL = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "reproduction_execution_input_closure_adapter",
    SKILL / "adapter" / "reproduction_execution_input_closure_adapter.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
run = module.run_reproduction_execution_input_closure


def _payload(*, readiness: str = "ready", resources: bool = True):
    commit = "1" * 40
    design = {
        "status": "ready",
        "repository": "https://example.invalid/repo.git",
        "revision": "2" * 40,
        "resource_acquisition_requests": [],
        "execution_resource_binding_plan": [],
        "executable": "Scripts/python.exe",
        "argv": ["train.py"],
        "working_directory": "repository",
        "timeout_seconds": 60,
        "environment_variables": {},
        "actual_metric_result_source": {"kind": "execution_stream", "stream": "stdout", "format": "json"},
        "actual_metric_requests": [{"metric_name": "score", "value_type": "number", "unit": None, "selector": {"json_pointer": "/score"}}],
    }
    payload = {
        "reproduction_experiment_design": design,
        "execution_plan_facts": {
            "schema_version": "openair-execution-plan/v1",
            "state": "succeeded",
            "plan_status": "complete",
            "execution_plan_id": "execplan:" + "a" * 64,
            "source_identity": {
                "repository": design["repository"],
                "resolved_commit_sha": design["revision"],
            },
            "resource_acquisition_requests": design["resource_acquisition_requests"],
            "execution_resource_binding_plan": design["execution_resource_binding_plan"],
            "executable": design["executable"],
            "argv": design["argv"],
            "working_directory": design["working_directory"],
            "timeout_seconds": design["timeout_seconds"],
            "environment_variables": design["environment_variables"],
            "actual_metric_result_source": design["actual_metric_result_source"],
            "actual_metric_requests": design["actual_metric_requests"],
        },
        "execution_readiness_record": {
            "status": readiness,
            "executable_scope": ["main"],
            "blocking_gaps": [],
        },
        "code_identity": {
            "state": "succeeded",
            "repository": design["repository"],
            "requested_revision": design["revision"],
            "resolved_commit_sha": "2" * 40,
            "verified_head": "2" * 40,
            "workspace_relative_path": "repository",
            "execution_evidence": {
                "git_version": "git version test",
                "head_matches_resolved_commit": True,
                "operations": [
                    {"operation": operation, "exit_code": 0}
                    for operation in ("clone", "resolve_revision", "checkout", "read_head")
                ],
            },
        },
        "environment_record": {
            "schema_version": "openair-environment-record/v1",
            "state": "succeeded",
            "environment_identity": {"fingerprint_sha256": "3" * 64},
            "workspace_relative_path": "environment",
            "environment_record_relative_path": "environment/environment-record.json",
        },
    }
    if resources:
        design["resource_acquisition_requests"] = [{
            "usage": "evaluation_model", "resource_kind": "huggingface_snapshot",
            "repo_id": "demo/model", "repo_type": "model", "revision": "main",
        }]
        design["execution_resource_binding_plan"] = [{
            "usage": "model", "resource_request_index": 0, "argv_index": 1,
        }]
        design["argv"].append("{{openair-resource:model}}")
        payload["execution_plan_facts"]["resource_acquisition_requests"] = design["resource_acquisition_requests"]
        payload["execution_plan_facts"]["execution_resource_binding_plan"] = design["execution_resource_binding_plan"]
        payload["execution_plan_facts"]["argv"] = design["argv"]
        payload["resource_identities"] = {
            "state": "succeeded",
            "resources": [{
                "usage": "evaluation_model",
                "result": {
                    "state": "succeeded", "resource_kind": "huggingface_snapshot",
                    "repo_id": "demo/model", "repo_type": "model",
                    "requested_revision": "main", "resolved_commit_sha": commit,
                    "actual_identity": {"kind": "huggingface_commit", "commit_sha": commit},
                    "expected_identity_verification": "not_requested",
                    "workspace_relative_path": "resource/model",
                    "execution_evidence": {"provider_client": "huggingface_hub", "operation": "snapshot_download", "materialized": True},
                },
            }],
            "summary": {"total": 1, "succeeded": 1, "failed": 0},
        }
    return payload


def test_ready_inputs_materialize_actual_resource_and_validate_for_runner():
    result = run(_payload())
    Draft7Validator(json.loads((SKILL / "output.schema.json").read_text(encoding="utf-8"))).validate(result)
    runner_schema = json.loads((SKILL.parent / "exploratory-run" / "input.schema.json").read_text(encoding="utf-8"))
    Draft7Validator(runner_schema).validate({key: result[key] for key in (
        "executable", "argv", "working_directory", "timeout_seconds", "environment_variables", "resource_bindings"
    )})
    extraction_schema = json.loads((SKILL.parent / "actual-metric-extraction" / "input.schema.json").read_text(encoding="utf-8"))
    Draft7Validator(extraction_schema).validate({
        "execution_record_relative_path": "execution/example/execution-record.json",
        "result_source": result["actual_metric_result_source"],
        "metrics": result["actual_metric_requests"],
    })
    assert result["resource_bindings"][0]["resource"]["resolved_commit_sha"] == "1" * 40


def test_not_ready_fails_before_an_execution_input_exists():
    assert run(_payload(readiness="not_ready")) == {
        "state": "failed", "failure_code": "execution_readiness_not_ready"
    }


def test_missing_or_mismatched_resource_fails_closed():
    payload = _payload()
    payload["resource_identities"]["resources"][0]["result"]["repo_id"] = "wrong/model"
    assert run(payload) == {"state": "failed", "failure_code": "resource_identity_mismatch"}


def test_no_resource_plan_needs_no_resource_result():
    result = run(_payload(resources=False))
    assert result["state"] == "succeeded" and result["resource_bindings"] == []


def test_experiment_design_code_copy_cannot_override_formal_plan():
    payload = _payload(resources=False)
    payload["execution_plan_facts"]["source_identity"]["resolved_commit_sha"] = "1" * 40
    assert run(payload) == {"state": "failed", "failure_code": "code_identity_mismatch"}

    payload = _payload(resources=False)
    payload["reproduction_experiment_design"]["revision"] = "1" * 40
    assert run(payload)["state"] == "succeeded"


def test_success_looking_inputs_still_require_acquisition_evidence():
    for mutation in ("missing_evidence", "head_failed", "missing_head"):
        payload = _payload(resources=False)
        code = payload["code_identity"]
        if mutation == "missing_evidence":
            del code["execution_evidence"]
        elif mutation == "head_failed":
            code["execution_evidence"]["head_matches_resolved_commit"] = False
        else:
            del code["verified_head"]
        assert run(payload) == {"state": "failed", "failure_code": "code_identity_mismatch"}

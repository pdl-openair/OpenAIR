from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import jsonschema
import pytest
import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from adapter import reproduction_record_assembly_adapter as adapter  # noqa: E402

EXECUTION_RECORD_RELATIVE_PATH = "execution/run-001/execution-record.json"


def _code() -> dict:
    commit = "1" * 40
    return {
        "state": "succeeded",
        "repository": "https://example.org/org/repo.git",
        "requested_revision": "v1.0.0",
        "resolved_commit_sha": commit,
        "verified_head": commit,
        "workspace_relative_path": "repository",
        "execution_evidence": {
            "git_version": "git version 2.51.0",
            "operations": [
                {"operation": "clone", "exit_code": 0},
                {"operation": "resolve_revision", "exit_code": 0},
                {"operation": "checkout", "exit_code": 0},
                {"operation": "read_head", "exit_code": 0},
            ],
            "head_matches_resolved_commit": True,
        },
    }


def _resource() -> dict:
    commit = "2" * 40
    return {
        "state": "succeeded",
        "resource_kind": "huggingface_snapshot",
        "repo_id": "org/model",
        "repo_type": "model",
        "requested_revision": "main",
        "resolved_commit_sha": commit,
        "actual_identity": {"kind": "huggingface_commit", "commit_sha": commit},
        "expected_identity_verification": "not_requested",
        "workspace_relative_path": "resource/model",
        "execution_evidence": {
            "provider_client": "huggingface_hub",
            "operation": "snapshot_download",
            "materialized": True,
        },
    }


def _environment() -> dict:
    facts = {
        "python": {
            "implementation": "CPython",
            "version": "3.11.9",
            "major": 3,
            "minor": 11,
            "micro": 9,
        },
        "platform": {
            "system": "Linux",
            "release": "6.8.0",
            "machine": "x86_64",
            "python_platform": "linux-x86_64",
        },
        "packages": [
            {"name": "numpy", "version": "2.0.0"},
            {"name": "pip", "version": "25.2"},
        ],
    }
    canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    identity = {**facts, "fingerprint_sha256": hashlib.sha256(canonical.encode()).hexdigest()}
    return {
        "schema_version": "openair-environment-record/v1",
        "state": "succeeded",
        "environment_identity": identity,
        "verification": {
            "requested_python_version": "3.11",
            "python_version_match": True,
            "pip_check": "passed",
            "imports": [{"module": "numpy", "status": "passed"}],
            "runtime_readiness": {"configured": False, "status": "not_configured"},
        },
        "execution_evidence": {
            "builder": {"kind": "venv", "version": "stdlib"},
            "operations": [
                {"operation": "create_environment", "exit_code": 0},
                {"operation": "install_requirements", "exit_code": 0},
                {"operation": "pip_check", "exit_code": 0},
                {"operation": "verify_imports", "exit_code": 0},
                {"operation": "read_environment_identity", "exit_code": 0},
            ],
            "pip_version": "25.2",
        },
        "workspace_relative_path": "environment",
        "environment_record_relative_path": "environment/environment-record.json",
    }


def _execution(environment: dict, *, state: str = "succeeded") -> dict:
    succeeded = state == "succeeded"
    stdout = '{"score": 0.91}\n'
    return {
        "schema_version": "openair-execution-record/v1",
        "state": state,
        "execution_status": "succeeded" if succeeded else "non_zero_exit",
        "actual_executable": "environment/bin/python",
        "argv": ["evaluate.py", "--split", "test"],
        "working_directory": "repository",
        "environment": {
            "workspace_relative_path": "environment",
            "environment_record_relative_path": "environment/environment-record.json",
            "fingerprint_sha256": environment["environment_identity"]["fingerprint_sha256"],
        },
        "environment_variable_names": [],
        "started_at_utc": "2026-09-11T01:00:00Z",
        "ended_at_utc": "2026-09-11T01:00:01Z",
        "duration_seconds": 1.0,
        "process_id": 123,
        "exit_code": 0 if succeeded else 2,
        "timed_out": False,
        "stdout": {
            "text": stdout,
            "total_bytes": len(stdout.encode()),
            "captured_bytes": len(stdout.encode()),
            "truncated": False,
            "stream_complete": True,
        },
        "stderr": {
            "text": "",
            "total_bytes": 0,
            "captured_bytes": 0,
            "truncated": False,
            "stream_complete": True,
        },
    }


def _consumption_evidence(tmp_path: Path, usage: str, consumed_path: str) -> dict:
    relative = f"execution/run-001/resource-consumption/{usage}.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(
            {
                "schema_version": "openair-resource-consumption-evidence/v1",
                "usage": usage,
                "consumed_workspace_relative_path": consumed_path,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    ).encode("utf-8")
    path.write_bytes(data)
    return {
        "kind": "workspace_json_receipt",
        "receipt_relative_path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _attach_resource_usage(execution: dict, resource: dict, tmp_path: Path) -> None:
    materialized = tmp_path / resource["workspace_relative_path"]
    materialized.mkdir(parents=True, exist_ok=True)
    (materialized / "marker.txt").write_text("resource", encoding="utf-8")
    execution["argv"] = ["evaluate.py", "--model", str(materialized.resolve())]
    planned = {
        "resource_kind": resource["resource_kind"],
        "repo_id": resource["repo_id"],
        "repo_type": resource["repo_type"],
        "resolved_commit_sha": resource["resolved_commit_sha"],
        "actual_identity": dict(resource["actual_identity"]),
        "workspace_relative_path": resource["workspace_relative_path"],
    }
    execution["resource_usage"] = [{
        "usage": resource["repo_type"],
        "planned_resource": planned,
        "delivery": {"kind": "argv_path", "argument_index": 2},
        "actual_usage": {
            "workspace_relative_path": resource["workspace_relative_path"],
            "immutable_identity": dict(resource["actual_identity"]),
        },
        "consumption_evidence": _consumption_evidence(
            tmp_path, resource["repo_type"], resource["workspace_relative_path"]
        ),
        "verification": "verified",
    }]


def _write_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: dict) -> str:
    path = tmp_path / EXECUTION_RECORD_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(tmp_path))
    return hashlib.sha256(data).hexdigest()


def _actual(execution_sha: str, *, complete: bool = True) -> dict:
    result_source = {
        "kind": "execution_stream",
        "stream": "stdout",
        "format": "json",
        "sha256": hashlib.sha256('{"score": 0.91}\n'.encode()).hexdigest(),
    }
    if complete:
        metric = {
            "metric_name": "quality_score",
            "actual_value": 0.91,
            "value_type": "number",
            "unit": None,
            "extraction_status": "succeeded",
            "source_trace": {
                "execution_record_relative_path": EXECUTION_RECORD_RELATIVE_PATH,
                "source_kind": "execution_stream",
                "source_sha256": result_source["sha256"],
                "format": "json",
                "stream": "stdout",
                "json_path": ["score"],
            },
        }
        return {
            "schema_version": "openair-actual-metrics/v1",
            "state": "succeeded",
            "extraction_status": "complete",
            "execution_record_relative_path": EXECUTION_RECORD_RELATIVE_PATH,
            "execution_record_sha256": execution_sha,
            "result_source": result_source,
            "requested_metric_count": 1,
            "extracted_metric_count": 1,
            "metrics": [metric],
        }
    return {
        "schema_version": "openair-actual-metrics/v1",
        "state": "failed",
        "extraction_status": "incomplete",
        "failure_code": "metric_extraction_incomplete",
        "failed_stage": "metrics",
        "execution_record_relative_path": EXECUTION_RECORD_RELATIVE_PATH,
        "execution_record_sha256": execution_sha,
        "result_source": result_source,
        "requested_metric_count": 1,
        "extracted_metric_count": 0,
        "metrics": [{
            "metric_name": "quality_score",
            "actual_value": None,
            "value_type": "number",
            "unit": None,
            "extraction_status": "failed",
            "failure_code": "metric_not_found",
        }],
    }


def _comparison(actual: dict, *, meets: bool = True, complete: bool = True) -> dict:
    source = actual["metrics"][0]
    actual_view = {
        "value": source.get("actual_value"),
        "value_type": source["value_type"],
        "unit": source["unit"],
        "extraction_status": source["extraction_status"],
    }
    if "failure_code" in source:
        actual_view["failure_code"] = source["failure_code"]
    if "source_trace" in source:
        actual_view["source_trace"] = source["source_trace"]
    if complete:
        return {
            "schema_version": "openair-reference-comparison/v1",
            "state": "succeeded",
            "comparison_status": "complete",
            "overall_result": "meets_standard" if meets else "does_not_meet_standard",
            "requested_reference_count": 1,
            "compared_metric_count": 1,
            "metrics": [{
                "metric_name": "quality_score",
                "comparison_status": "succeeded",
                "meets_rule": meets,
                "actual": actual_view,
                "reference": {"value": 0.9, "value_type": "number", "unit": None},
                "rule": {"kind": "actual_gte_reference"},
                "calculation_trace": {"actual_decimal": "0.91", "reference_decimal": "0.9"},
            }],
        }
    return {
        "schema_version": "openair-reference-comparison/v1",
        "state": "failed",
        "comparison_status": "incomplete",
        "overall_result": "indeterminate",
        "failure_code": "comparison_incomplete",
        "requested_reference_count": 1,
        "compared_metric_count": 0,
        "metrics": [{
            "metric_name": "quality_score",
            "comparison_status": "failed",
            "meets_rule": None,
            "failure_code": "actual_metric_unavailable",
            "actual": actual_view,
            "reference": {"value": 0.9, "value_type": "number", "unit": None},
            "rule": {"kind": "actual_gte_reference"},
        }],
    }


def _payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    environment = _environment()
    resource = _resource()
    execution = _execution(environment)
    _attach_resource_usage(execution, resource, tmp_path)
    digest = _write_execution(tmp_path, monkeypatch, execution)
    actual = _actual(digest)
    return {
        "code_identity": _code(),
        "resource_identities": [resource],
        "environment_record": environment,
        "execution_record": execution,
        "actual_metrics": actual,
        "reference_comparison": _comparison(actual),
    }


def _assert_output_schema(result: dict) -> None:
    schema = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)


def test_complete_record_meets_standard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = adapter.run_reproduction_record_assembly(_payload(tmp_path, monkeypatch))
    _assert_output_schema(result)
    assert result["state"] == "succeeded"
    assert result["record_status"] == "complete"
    assert result["reproduction_result"] == "meets_standard"
    assert result["missing_components"] == []
    assert "packages" in result["environment"]["environment_identity"]
    assert "stdout" in result["execution"]
    assert result["actual_metrics"]["metrics"][0]["source_trace"]["source_sha256"]


def test_complete_record_does_not_meet_standard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(tmp_path, monkeypatch)
    payload["reference_comparison"] = _comparison(payload["actual_metrics"], meets=False)
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["state"] == "succeeded"
    assert result["record_status"] == "complete"
    assert result["reproduction_result"] == "does_not_meet_standard"


def test_missing_reference_comparison_is_incomplete_and_indeterminate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(tmp_path, monkeypatch)
    payload["reference_comparison"] = None
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["state"] == "succeeded"
    assert result["record_status"] == "incomplete"
    assert result["reproduction_result"] == "indeterminate"
    assert result["missing_components"] == ["reference_comparison"]
    assert result["actual_metrics"] is not None


def test_canonical_execution_record_without_embedded_path_is_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(tmp_path, monkeypatch)
    assert "execution_record_relative_path" not in payload["execution_record"]
    payload["reference_comparison"] = None
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["state"] == "succeeded"
    assert result["record_status"] == "incomplete"
    assert result["reproduction_result"] == "indeterminate"
    assert result["missing_components"] == ["reference_comparison"]


def test_actual_metrics_incomplete_preserves_other_trusted_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(tmp_path, monkeypatch)
    payload["actual_metrics"] = _actual(
        hashlib.sha256((tmp_path / EXECUTION_RECORD_RELATIVE_PATH).read_bytes()).hexdigest(),
        complete=False,
    )
    payload["reference_comparison"] = None
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["state"] == "succeeded"
    assert result["record_status"] == "incomplete"
    assert result["missing_components"] == ["actual_metrics", "reference_comparison"]
    assert result["code_identity"]["resolved_commit_sha"] == "1" * 40
    assert result["execution"]["execution_status"] == "succeeded"
    assert result["actual_metrics"]["extraction_status"] == "incomplete"


def test_comparison_incomplete_is_incomplete_not_failed_assembly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(tmp_path, monkeypatch)
    payload["reference_comparison"] = _comparison(payload["actual_metrics"], complete=False)
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["state"] == "succeeded"
    assert result["record_status"] == "incomplete"
    assert result["reproduction_result"] == "indeterminate"
    assert result["missing_components"] == ["reference_comparison"]


def test_invalid_outer_structure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(tmp_path, monkeypatch)
    del payload["execution_record"]
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["state"] == "failed"
    assert result["failure_code"] == "skill_input_invalid"


def test_skill_contract_is_deterministic_and_generic() -> None:
    skill = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))
    assert skill["runtime_contract"] == {
        "kind": "deterministic",
        "requires_external_action": False,
        "adapter_id": "python_adapter:reproduction_record_assembly",
        "evidence_contract_version": "deterministic-output/v1",
    }
    production = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (SKILL_ROOT / "adapter").glob("*.py")
    )
    for forbidden in ["simcse", "senteval", "stsbenchmark", "princeton"]:
        assert forbidden not in production
    for forbidden in ["import openai", "from openai", "import llm", "from llm", "anthropic", "artifact_catalog", "artifact_repository", "worker"]:
        assert forbidden not in production


def _rewrite_execution_for_payload(
    payload: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload["actual_metrics"]["execution_record_sha256"] = _write_execution(
        tmp_path,
        monkeypatch,
        payload["execution_record"],
    )


def _dataset_resource() -> dict:
    commit = "3" * 40
    return {
        "state": "succeeded",
        "resource_kind": "huggingface_snapshot",
        "repo_id": "org/dataset",
        "repo_type": "dataset",
        "requested_revision": "main",
        "resolved_commit_sha": commit,
        "actual_identity": {"kind": "huggingface_commit", "commit_sha": commit},
        "expected_identity_verification": "not_requested",
        "workspace_relative_path": "resource/dataset",
        "execution_evidence": {
            "provider_client": "huggingface_hub",
            "operation": "snapshot_download",
            "materialized": True,
        },
    }


def _usage_entry(
    resource: dict,
    usage: str,
    argument_index: int,
    tmp_path: Path,
    *,
    verification: str = "verified",
    consumed_path: str | None = None,
) -> dict:
    entry = {
        "usage": usage,
        "planned_resource": {
            "resource_kind": resource["resource_kind"],
            "repo_id": resource["repo_id"],
            "repo_type": resource["repo_type"],
            "resolved_commit_sha": resource["resolved_commit_sha"],
            "actual_identity": dict(resource["actual_identity"]),
            "workspace_relative_path": resource["workspace_relative_path"],
        },
        "delivery": {"kind": "argv_path", "argument_index": argument_index},
        "verification": verification,
    }
    if verification == "unverified":
        return entry
    actual_path = consumed_path or resource["workspace_relative_path"]
    entry["consumption_evidence"] = _consumption_evidence(tmp_path, usage, actual_path)
    if actual_path == resource["workspace_relative_path"]:
        entry["actual_usage"] = {
            "workspace_relative_path": actual_path,
            "immutable_identity": dict(resource["actual_identity"]),
        }
    return entry


def _file_dataset_resource(tmp_path: Path) -> dict:
    commit = "4" * 40
    relative = "resource/dataset/senteval.tar"
    content = b"fixed-archive-bytes"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    return {
        "state": "succeeded",
        "resource_kind": "huggingface_file",
        "repo_id": "org/dataset-archive",
        "repo_type": "dataset",
        "requested_revision": "fixed",
        "resolved_commit_sha": commit,
        "actual_identity": {
            "kind": "huggingface_file_at_commit",
            "commit_sha": commit,
            "filename": "senteval.tar",
            "sha256": digest,
        },
        "expected_identity_verification": "passed",
        "workspace_relative_path": relative,
        "execution_evidence": {
            "provider_client": "huggingface_hub",
            "operation": "hf_hub_download",
            "materialized": True,
        },
    }


def _delivery_manifest(
    tmp_path: Path,
    usage: str,
    archive_sha256: str,
    files: list[dict],
) -> dict:
    relative = f"execution/run-001/resource-delivery/{usage}.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "openair-resource-delivery-manifest/v1",
        "archive_sha256": archive_sha256,
        "target_working_directory": "repository",
        "files": files,
    }
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(data)
    return {
        "kind": "tar_extract_working_directory",
        "target_working_directory": "repository",
        "archive_sha256": archive_sha256,
        "manifest_sha256": hashlib.sha256(data).hexdigest(),
        "manifest_relative_path": relative,
        "member_count": len(files),
        "file_count": len(files),
        "total_file_bytes": sum(row["size"] for row in files),
    }


def _access_evidence(
    tmp_path: Path,
    usage: str,
    planned_path: str,
    accessed_path: str,
    *,
    manifest_sha256: str | None = None,
) -> dict:
    relative = f"execution/run-001/resource-consumption/{usage}.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "openair-resource-consumption-evidence/v2",
        "usage": usage,
        "evidence_kind": "controlled_python_file_open_read",
        "planned_resource_workspace_relative_path": planned_path,
        "accessed_workspace_relative_path": accessed_path,
    }
    if manifest_sha256 is not None:
        payload["delivery_manifest_sha256"] = manifest_sha256
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.write_bytes(data)
    return {
        "kind": "controlled_python_file_open_read",
        "receipt_relative_path": relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "accessed_workspace_relative_path": accessed_path,
    }


def _archive_usage_entry(resource: dict, tmp_path: Path, accessed_path: str) -> dict:
    file_content = b"dataset-row"
    files = [{
        "workspace_relative_path": "repository/SentEval/data/sample.txt",
        "size": len(file_content),
        "sha256": hashlib.sha256(file_content).hexdigest(),
    }]
    delivery = _delivery_manifest(
        tmp_path,
        "dataset",
        resource["actual_identity"]["sha256"],
        files,
    )
    evidence = _access_evidence(
        tmp_path,
        "dataset",
        resource["workspace_relative_path"],
        accessed_path,
        manifest_sha256=delivery["manifest_sha256"],
    )
    return {
        "usage": "dataset",
        "planned_resource": {
            "resource_kind": resource["resource_kind"],
            "repo_id": resource["repo_id"],
            "repo_type": resource["repo_type"],
            "resolved_commit_sha": resource["resolved_commit_sha"],
            "actual_identity": dict(resource["actual_identity"]),
            "workspace_relative_path": resource["workspace_relative_path"],
        },
        "delivery": delivery,
        "actual_usage": {
            "workspace_relative_path": resource["workspace_relative_path"],
            "immutable_identity": dict(resource["actual_identity"]),
            "observed_access": {
                "kind": "python_file_open_read",
                "workspace_relative_path": accessed_path,
            },
        },
        "consumption_evidence": evidence,
        "verification": "verified",
    }


def test_scientific_metadata_and_hash_differences_do_not_erase_raw_records(tmp_path, monkeypatch):
    payload = _payload(tmp_path, monkeypatch)
    payload["execution_record"]["execution_record_sha256"] = "原始摘要"
    payload["code_identity"]["verified_head"] = "另一版本记录"
    payload["actual_metrics"]["metrics"][0]["actual_value"] = "原始数值文本"
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["execution"] == payload["execution_record"]
    assert result["code_identity"] == payload["code_identity"]
    assert result["actual_metrics"] == payload["actual_metrics"]


def test_failed_process_record_remains_failed_and_incomplete(tmp_path, monkeypatch):
    payload = _payload(tmp_path, monkeypatch)
    payload["execution_record"]["state"] = "failed"
    result = adapter.run_reproduction_record_assembly(payload)
    assert result["execution"]["state"] == "failed"
    assert result["record_status"] == "incomplete"
    assert "execution_record" in result["missing_components"]

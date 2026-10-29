from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import venv
from typing import Any

import jsonschema
import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from adapter import execution_common as common  # noqa: E402
from adapter import exploratory_run_adapter as adapter  # noqa: E402


def _python_relative() -> str:
    return "Scripts/python.exe" if os.name == "nt" else "bin/python"


def _prepare_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    environment = tmp_path / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    record = {
        "schema_version": "openair-environment-record/v1",
        "state": "succeeded",
        "environment_identity": {
            "python": {
                "implementation": "CPython",
                "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "major": sys.version_info.major,
                "minor": sys.version_info.minor,
                "micro": sys.version_info.micro,
            },
            "platform": {
                "system": "test",
                "release": "test",
                "machine": "test",
                "python_platform": "test",
            },
            "packages": [],
            "fingerprint_sha256": "0" * 64,
        },
        "verification": {},
        "execution_evidence": {},
        "workspace_relative_path": "environment",
        "environment_record_relative_path": "environment/environment-record.json",
    }
    (environment / "environment-record.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    (tmp_path / "repository").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(tmp_path))
    return _python_relative()


def _request(executable: str, **updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "executable": executable,
        "argv": ["-c", "print('ok')"],
        "working_directory": "repository",
        "timeout_seconds": 10,
    }
    payload.update(updates)
    return payload


def _record(tmp_path: Path, result: dict[str, Any]) -> dict[str, Any]:
    path = tmp_path / result["execution_record_relative_path"]
    assert path.is_file()
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            capture_output=True,
            check=False,
            shell=False,
        )
        return result.returncode == 0 and f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_real_subprocess_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(_request(executable))
    assert result["state"] == "succeeded"
    assert result["execution_status"] == "succeeded"
    assert result["exit_code"] == 0
    assert result["stdout"]["text"].strip() == "ok"
    assert _record(tmp_path, result)["exit_code"] == 0


def test_argv_is_parameterized_not_shell_interpreted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    marker = tmp_path / "must-not-exist.txt"
    suspicious = f"; echo injected > {marker}"
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "import sys; print(sys.argv[1])", suspicious],
        )
    )
    assert result["state"] == "succeeded"
    assert suspicious in result["stdout"]["text"]
    assert not marker.exists()


def test_working_directory_escape_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(_request(executable, working_directory="../outside"))
    assert result["failure_code"] == "working_directory_invalid"
    assert result["failed_stage"] == "working_directory"


def test_missing_executable_is_distinct_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(_request("Scripts/missing.exe" if os.name == "nt" else "bin/missing"))
    assert result["failure_code"] == "executable_not_found_or_invalid"
    assert result["failed_stage"] == "executable"


def test_non_zero_exit_is_real_failed_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(
        _request(executable, argv=["-c", "import sys; sys.exit(7)"])
    )
    assert result["state"] == "failed"
    assert result["execution_status"] == "non_zero_exit"
    assert result["failure_code"] == "non_zero_exit"
    assert result["exit_code"] == 7
    assert _record(tmp_path, result)["exit_code"] == 7


def test_stdout_and_stderr_are_captured_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "import sys; print('stdout-fact'); sys.stderr.write('stderr-fact\\n')"],
        )
    )
    assert "stdout-fact" in result["stdout"]["text"]
    assert "stderr-fact" in result["stderr"]["text"]


def test_stderr_warning_with_exit_zero_is_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(
        _request(executable, argv=["-c", "import sys; sys.stderr.write('warning only\\n')"])
    )
    assert result["state"] == "succeeded"
    assert result["exit_code"] == 0
    assert "warning only" in result["stderr"]["text"]


def test_timeout_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(
        _request(executable, argv=["-c", "import time; time.sleep(30)"], timeout_seconds=1)
    )
    assert result["state"] == "failed"
    assert result["execution_status"] == "timeout"
    assert result["timed_out"] is True
    assert result["failure_code"] == "execution_timeout"


def test_timeout_terminates_child_process_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "print(p.pid, flush=True); time.sleep(30)"
    )
    result = adapter.run_exploratory_run(
        _request(executable, argv=["-c", code], timeout_seconds=1)
    )
    assert result["execution_status"] == "timeout"
    child_pid = int(result["stdout"]["text"].strip().splitlines()[0])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _pid_exists(child_pid):
        time.sleep(0.1)
    assert not _pid_exists(child_pid)


def test_execution_record_uses_actual_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    argv = ["-c", "print('facts')"]
    result = adapter.run_exploratory_run(_request(executable, argv=argv))
    persisted = _record(tmp_path, result)
    assert persisted["actual_executable"] == f"environment/{executable}"
    assert persisted["argv"] == argv
    assert persisted["working_directory"] == "repository"
    assert persisted["environment"]["fingerprint_sha256"] == "0" * 64
    assert persisted["started_at_utc"]
    assert persisted["ended_at_utc"]
    assert persisted["duration_seconds"] >= 0


def test_sensitive_environment_value_is_not_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    secret = "openair-super-secret-value"
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "import os; print(os.environ['OPENAIR_TEST_TOKEN'])"],
            environment_variables={"OPENAIR_TEST_TOKEN": secret},
        )
    )
    serialized = json.dumps(result)
    persisted = json.dumps(_record(tmp_path, result))
    assert secret not in serialized
    assert secret not in persisted
    assert "<redacted>" in result["stdout"]["text"]
    assert result["environment_variable_names"] == ["OPENAIR_TEST_TOKEN"]


def test_sensitive_environment_value_is_redacted_from_recorded_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    secret = "openair-argv-secret-value"
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "import sys; print(sys.argv[1])", secret],
            environment_variables={"OPENAIR_TEST_TOKEN": secret},
        )
    )
    persisted = _record(tmp_path, result)
    assert secret not in json.dumps(result)
    assert secret not in json.dumps(persisted)
    assert result["argv"][-1] == "<redacted>"
    assert persisted["argv"][-1] == "<redacted>"


def test_stdout_capture_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(
        _request(executable, argv=["-c", "print('x' * 100000)"])
    )
    assert result["state"] == "succeeded"
    assert result["stdout"]["captured_bytes"] <= common.CAPTURE_LIMIT_BYTES
    assert result["stdout"]["total_bytes"] > result["stdout"]["captured_bytes"]
    assert result["stdout"]["truncated"] is True


def test_process_start_failure_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_workspace(tmp_path, monkeypatch)
    environment = tmp_path / "environment"
    bad = environment / ("broken.exe" if os.name == "nt" else "broken")
    bad.write_text("not executable", encoding="utf-8")
    if os.name != "nt":
        bad.chmod(0o644)
    result = adapter.run_exploratory_run(_request(bad.name))
    assert result["state"] == "failed"
    assert result["execution_status"] == "process_start_failed"
    assert result["failure_code"] == "process_start_failed"
    assert result["exit_code"] is None
    assert _record(tmp_path, result)["execution_status"] == "process_start_failed"


def test_process_start_failure_does_not_claim_resource_actual_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_workspace(tmp_path, monkeypatch)
    environment = tmp_path / "environment"
    bad = environment / ("broken-resource.exe" if os.name == "nt" else "broken-resource")
    bad.write_text("not executable", encoding="utf-8")
    if os.name != "nt":
        bad.chmod(0o644)
    model_path = _prepare_resource(tmp_path, "model-start-failure", "model-ok")
    result = adapter.run_exploratory_run(
        _request(
            bad.name,
            argv=["{{openair-resource:model}}"],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 0,
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )
    assert result["execution_status"] == "process_start_failed"
    assert "resource_usage" not in result
    assert "resource_usage" not in _record(tmp_path, result)


@pytest.mark.parametrize("case", ["adapter_failure", "adapter_exception"])
def test_termination_failure_is_not_reported_as_timeout_successfully_killed(
    tmp_path: Path, case: str
) -> None:
    import runpy

    helper = runpy.run_path(str(Path(__file__).with_name("test_execution_process_cleanup.py")))
    helper["supervise_cleanup_case"](case, tmp_path)


def test_production_adapter_has_no_simcse_hardcoding() -> None:
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SKILL_ROOT / "adapter").rglob("*.py")
    ).lower()
    assert "simcse" not in production
    assert "senteval" not in production
    assert "princeton" not in production


def _formal_resource(repo_type: str, relative_path: str, *, commit: str = "1" * 40) -> dict[str, Any]:
    return {
        "state": "succeeded",
        "resource_kind": "huggingface_snapshot",
        "repo_id": f"example/{repo_type}",
        "repo_type": repo_type,
        "requested_revision": commit,
        "resolved_commit_sha": commit,
        "actual_identity": {"kind": "huggingface_commit", "commit_sha": commit},
        "expected_identity_verification": "passed",
        "workspace_relative_path": relative_path,
        "execution_evidence": {
            "provider_client": "huggingface_hub 1.26.0",
            "operation": "snapshot_download",
            "materialized": True,
        },
    }


def _prepare_resource(tmp_path: Path, name: str, marker_text: str) -> str:
    root = tmp_path / "resource" / name
    root.mkdir(parents=True)
    (root / "marker.txt").write_text(marker_text, encoding="utf-8")
    return root.relative_to(tmp_path).as_posix()


def _prepare_tar_resource(
    tmp_path: Path,
    name: str,
    members: list[tuple[tarfile.TarInfo, bytes | None]],
) -> tuple[str, str]:
    root = tmp_path / "resource" / name
    root.mkdir(parents=True)
    archive_path = root / "dataset.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        for info, content in members:
            archive.addfile(info, io.BytesIO(content) if content is not None else None)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return archive_path.relative_to(tmp_path).as_posix(), digest


def _file_resource(repo_type: str, relative_path: str, sha256: str, *, commit: str = "2" * 40) -> dict[str, Any]:
    return {
        "state": "succeeded",
        "resource_kind": "huggingface_file",
        "repo_id": f"example/{repo_type}-archive",
        "repo_type": repo_type,
        "requested_revision": commit,
        "resolved_commit_sha": commit,
        "actual_identity": {
            "kind": "huggingface_file_at_commit",
            "commit_sha": commit,
            "filename": "dataset.tar",
            "sha256": sha256,
        },
        "expected_identity_verification": "passed",
        "workspace_relative_path": relative_path,
        "execution_evidence": {
            "provider_client": "huggingface_hub 1.26.0",
            "operation": "hf_hub_download",
            "materialized": True,
        },
    }


def _tar_file(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    return info, content


def test_single_resource_is_injected_as_fixed_path_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-fixed", "model-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=[
                "-c",
                "import pathlib,sys; print(pathlib.Path(sys.argv[1], 'marker.txt').read_text())",
                "{{openair-resource:model}}",
            ],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )
    assert result["state"] == "succeeded"
    assert result["stdout"]["text"].strip() == "model-ok"
    assert Path(result["argv"][2]).resolve() == (tmp_path / model_path).resolve()
    assert result["resource_usage"][0]["usage"] == "model"
    assert result["resource_usage"][0]["verification"] == "unverified"
    assert "actual_usage" not in result["resource_usage"][0]
    assert "consumption_evidence" not in result["resource_usage"][0]
    assert _record(tmp_path, result)["resource_usage"] == result["resource_usage"]


def test_model_and_dataset_are_independently_injected_in_one_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-fixed", "model-ok")
    dataset_path = _prepare_resource(tmp_path, "dataset-fixed", "dataset-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=[
                "-c",
                (
                    "import pathlib,sys; "
                    "print(pathlib.Path(sys.argv[1], 'marker.txt').read_text()); "
                    "print(pathlib.Path(sys.argv[2], 'marker.txt').read_text())"
                ),
                "{{openair-resource:model}}",
                "{{openair-resource:dataset}}",
            ],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", model_path, commit="1" * 40),
                },
                {
                    "usage": "dataset",
                    "argv_index": 3,
                    "resource": _formal_resource("dataset", dataset_path, commit="2" * 40),
                },
            ],
        )
    )
    assert result["state"] == "succeeded"
    assert result["stdout"]["text"].splitlines() == ["model-ok", "dataset-ok"]
    assert [item["usage"] for item in result["resource_usage"]] == ["model", "dataset"]
    assert result["resource_usage"][0]["planned_resource"]["resolved_commit_sha"] == "1" * 40
    assert result["resource_usage"][1]["planned_resource"]["resolved_commit_sha"] == "2" * 40
    assert [item["verification"] for item in result["resource_usage"]] == ["unverified", "unverified"]


def test_model_and_dataset_cannot_share_one_formal_resource_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    shared_path = _prepare_resource(tmp_path, "shared-old-resource", "shared")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=[
                "-c",
                "print('must-not-run')",
                "{{openair-resource:model}}",
                "{{openair-resource:dataset}}",
            ],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", shared_path, commit="1" * 40),
                },
                {
                    "usage": "dataset",
                    "argv_index": 3,
                    "resource": _formal_resource("dataset", shared_path, commit="2" * 40),
                },
            ],
        )
    )
    assert result["failure_code"] == "skill_input_invalid"
    assert not (tmp_path / "execution").exists()


def test_missing_bound_resource_fails_before_process_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    missing = "resource/missing-model"
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "print('must-not-run')", "{{openair-resource:model}}"],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", missing),
                }
            ],
        )
    )
    assert result == {
        "state": "failed",
        "failure_code": "resource_binding_unavailable",
        "failed_stage": "resource_binding",
    }
    assert not (tmp_path / "execution").exists()


def test_resource_binding_requires_controlled_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-fixed", "model-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "print('must-not-run')", "sentence-transformers/latest"],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )
    assert result["failure_code"] == "skill_input_invalid"
    assert not (tmp_path / "execution").exists()


def test_unbound_execution_remains_resource_agnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    result = adapter.run_exploratory_run(_request(executable))
    assert result["state"] == "succeeded"
    assert "resource_usage" not in result
    assert "resource_usage" not in _record(tmp_path, result)


def test_bound_execution_matches_formal_input_and_output_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-schema", "model-ok")
    payload = _request(
        executable,
        argv=[
            "-c",
            "import pathlib,sys; print(pathlib.Path(sys.argv[1], 'marker.txt').read_text())",
            "{{openair-resource:model}}",
        ],
        resource_bindings=[
            {
                "usage": "model",
                "argv_index": 2,
                "resource": _formal_resource("model", model_path),
            }
        ],
    )
    input_schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(payload, input_schema)

    result = adapter.run_exploratory_run(payload)
    output_schema = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, output_schema)
    assert result["resource_usage"][0]["verification"] == "unverified"


def _receipt_writer_program(usage: str, *, consumed_argument: int) -> str:
    env_name = f"OPENAIR_RESOURCE_CONSUMPTION_EVIDENCE_{usage.upper()}"
    return (
        "import json,os,pathlib,sys; "
        f"resource=pathlib.Path(sys.argv[{consumed_argument}]).resolve(); "
        "marker=(resource/'marker.txt').read_text(); "
        "workspace=pathlib.Path(os.environ['OPENAIR_ADAPTER_EXECUTION_CWD']).resolve(); "
        f"receipt=pathlib.Path(os.environ['{env_name}']); "
        f"payload={{'schema_version':'openair-resource-consumption-evidence/v1','usage':'{usage}',"
        "'consumed_workspace_relative_path':resource.relative_to(workspace).as_posix()}; "
        "receipt.write_text(json.dumps(payload),encoding='utf-8'); "
        "print(marker)"
    )


def test_deterministic_consumption_receipt_allows_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-consumed", "model-consumed-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", _receipt_writer_program("model", consumed_argument=1), "{{openair-resource:model}}"],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )

    assert result["state"] == "succeeded"
    usage = result["resource_usage"][0]
    assert usage["verification"] == "verified"
    assert usage["actual_usage"]["workspace_relative_path"] == model_path
    assert usage["consumption_evidence"]["kind"] == "workspace_json_receipt"
    assert (tmp_path / usage["consumption_evidence"]["receipt_relative_path"]).is_file()
    output_schema = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, output_schema)


def test_consumption_receipt_for_another_resource_is_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-planned", "model-planned")
    other_path = _prepare_resource(tmp_path, "model-actually-consumed", "other-consumed")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=[
                "-c",
                _receipt_writer_program("model", consumed_argument=2),
                "{{openair-resource:model}}",
                str((tmp_path / other_path).resolve()),
            ],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )

    assert result["state"] == "succeeded"
    usage = result["resource_usage"][0]
    assert usage["verification"] == "mismatch"
    assert "actual_usage" not in usage
    assert usage["consumption_evidence"]["kind"] == "workspace_json_receipt"


def test_model_and_dataset_can_have_different_verification_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-mixed", "model-ok")
    dataset_path = _prepare_resource(tmp_path, "dataset-mixed", "dataset-ok")
    program = (
        "import json,os,pathlib,sys; "
        "model=pathlib.Path(sys.argv[1]).resolve(); dataset=pathlib.Path(sys.argv[2]).resolve(); "
        "print((model/'marker.txt').read_text()); print((dataset/'marker.txt').read_text()); "
        "workspace=pathlib.Path(os.environ['OPENAIR_ADAPTER_EXECUTION_CWD']).resolve(); "
        "receipt=pathlib.Path(os.environ['OPENAIR_RESOURCE_CONSUMPTION_EVIDENCE_MODEL']); "
        "payload={'schema_version':'openair-resource-consumption-evidence/v1','usage':'model',"
        "'consumed_workspace_relative_path':model.relative_to(workspace).as_posix()}; "
        "receipt.write_text(json.dumps(payload),encoding='utf-8')"
    )
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", program, "{{openair-resource:model}}", "{{openair-resource:dataset}}"],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", model_path, commit="1" * 40),
                },
                {
                    "usage": "dataset",
                    "argv_index": 3,
                    "resource": _formal_resource("dataset", dataset_path, commit="2" * 40),
                },
            ],
        )
    )

    assert result["state"] == "succeeded"
    assert [item["verification"] for item in result["resource_usage"]] == ["verified", "unverified"]
    assert "actual_usage" in result["resource_usage"][0]
    assert "actual_usage" not in result["resource_usage"][1]


def test_reserved_consumption_evidence_environment_cannot_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-reserved-env", "model-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "print('must-not-run')", "{{openair-resource:model}}"],
            environment_variables={"OPENAIR_RESOURCE_CONSUMPTION_EVIDENCE_MODEL": "spoofed"},
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )
    assert result["failure_code"] == "skill_input_invalid"


def test_setfit_v040_style_dataset_delivery_without_consumption_proof_is_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    dataset_path = _prepare_resource(tmp_path, "setfit-style-dataset", "dataset-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=[
                "-c",
                "import sys; print('SetFit/' + sys.argv[1])",
                "{{openair-resource:dataset}}",
            ],
            resource_bindings=[
                {
                    "usage": "dataset",
                    "argv_index": 2,
                    "resource": _formal_resource("dataset", dataset_path),
                }
            ],
        )
    )

    assert result["state"] == "succeeded"
    assert "SetFit/" in result["stdout"]["text"]
    assert Path(result["argv"][2]).resolve() == (tmp_path / dataset_path).resolve()
    usage = result["resource_usage"][0]
    assert usage["verification"] == "unverified"
    assert "actual_usage" not in usage
    assert "consumption_evidence" not in usage


def test_tar_resource_is_delivered_to_working_directory_without_claiming_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    existing_code = tmp_path / "repository" / "SentEval" / "senteval"
    existing_code.mkdir(parents=True)
    (existing_code / "sts.py").write_text("# existing code\n", encoding="utf-8")
    relative_path, digest = _prepare_tar_resource(
        tmp_path,
        "dataset-archive",
        [_tar_file("SentEval/data/downstream/sample.txt", b"dataset-ok")],
    )
    payload = _request(
        executable,
        argv=[
            "-c",
            "import pathlib; print(pathlib.Path('SentEval/data/downstream/sample.txt').read_text())",
        ],
        resource_bindings=[
            {
                "usage": "dataset",
                "delivery": {"kind": "tar_extract_working_directory"},
                "resource": _file_resource("dataset", relative_path, digest),
            }
        ],
    )
    jsonschema.validate(payload, json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8")))

    result = adapter.run_exploratory_run(payload)

    assert result["state"] == "succeeded"
    assert result["stdout"]["text"].strip() == "dataset-ok"
    usage = result["resource_usage"][0]
    assert usage["verification"] == "unverified"
    assert usage["delivery"]["kind"] == "tar_extract_working_directory"
    assert usage["delivery"]["archive_sha256"] == digest
    assert usage["delivery"]["file_count"] == 1
    assert usage["delivery"]["total_file_bytes"] == len(b"dataset-ok")
    manifest_path = tmp_path / usage["delivery"]["manifest_relative_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == usage["delivery"]["manifest_sha256"]
    assert manifest["archive_sha256"] == digest
    assert manifest["files"] == [{
        "sha256": hashlib.sha256(b"dataset-ok").hexdigest(),
        "size": len(b"dataset-ok"),
        "workspace_relative_path": "repository/SentEval/data/downstream/sample.txt",
    }]
    assert "actual_usage" not in usage
    assert "consumption_evidence" not in usage
    assert (existing_code / "sts.py").is_file()
    assert not (tmp_path / "repository" / "SentEval" / "data" / "downstream" / "sample.txt").exists()
    jsonschema.validate(result, json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8")))


def test_tar_delivery_rejects_path_traversal_before_process_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    relative_path, digest = _prepare_tar_resource(
        tmp_path,
        "traversal-archive",
        [_tar_file("../escape.txt", b"no")],
    )
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "print('must-not-run')"],
            resource_bindings=[
                {
                    "usage": "dataset",
                    "delivery": {"kind": "tar_extract_working_directory"},
                    "resource": _file_resource("dataset", relative_path, digest),
                }
            ],
        )
    )
    assert result["failure_code"] == "resource_binding_unavailable"
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "execution").exists()


def test_tar_delivery_rejects_links_and_existing_file_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    existing = tmp_path / "repository" / "SentEval"
    existing.mkdir()
    (existing / "keep.py").write_text("keep", encoding="utf-8")

    collision_path, collision_digest = _prepare_tar_resource(
        tmp_path,
        "collision-archive",
        [_tar_file("SentEval/keep.py", b"overwrite")],
    )
    collision = adapter.run_exploratory_run(
        _request(
            executable,
            resource_bindings=[
                {
                    "usage": "dataset",
                    "delivery": {"kind": "tar_extract_working_directory"},
                    "resource": _file_resource("dataset", collision_path, collision_digest),
                }
            ],
        )
    )
    assert collision["failure_code"] == "resource_binding_unavailable"
    assert (existing / "keep.py").read_text(encoding="utf-8") == "keep"

    link = tarfile.TarInfo("SentEval/data/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../outside"
    link_path, link_digest = _prepare_tar_resource(tmp_path, "link-archive", [(link, None)])
    linked = adapter.run_exploratory_run(
        _request(
            executable,
            resource_bindings=[
                {
                    "usage": "dataset",
                    "delivery": {"kind": "tar_extract_working_directory"},
                    "resource": _file_resource("dataset", link_path, link_digest),
                }
            ],
        )
    )
    assert linked["failure_code"] == "resource_binding_unavailable"


def test_tar_delivery_rechecks_formal_archive_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    relative_path, _ = _prepare_tar_resource(
        tmp_path,
        "hash-mismatch-archive",
        [_tar_file("SentEval/data/value.txt", b"value")],
    )
    result = adapter.run_exploratory_run(
        _request(
            executable,
            resource_bindings=[
                {
                    "usage": "dataset",
                    "delivery": {"kind": "tar_extract_working_directory"},
                    "resource": _file_resource("dataset", relative_path, "f" * 64),
                }
            ],
        )
    )
    assert result["failure_code"] == "resource_binding_unavailable"
    assert not (tmp_path / "repository" / "SentEval").exists()


def test_tar_delivery_requires_formal_file_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    snapshot_path = _prepare_resource(tmp_path, "snapshot-not-archive", "value")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            resource_bindings=[
                {
                    "usage": "dataset",
                    "delivery": {"kind": "tar_extract_working_directory"},
                    "resource": _formal_resource("dataset", snapshot_path),
                }
            ],
        )
    )
    assert result["failure_code"] == "skill_input_invalid"


def test_explicit_python_file_open_probe_verifies_direct_resource_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-probed", "model-probed-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=[
                "-c",
                "import pathlib,sys; print(pathlib.Path(sys.argv[1], 'marker.txt').read_text())",
                "{{openair-resource:model}}",
            ],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "consumption_probe": {"kind": "python_file_open_read"},
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )

    assert result["state"] == "succeeded"
    assert result["stdout"]["text"].strip() == "model-probed-ok"
    usage = result["resource_usage"][0]
    assert usage["verification"] == "verified"
    assert usage["consumption_evidence"]["kind"] == "controlled_python_file_open_read"
    assert usage["consumption_evidence"]["accessed_workspace_relative_path"] == f"{model_path}/marker.txt"
    assert usage["actual_usage"]["workspace_relative_path"] == model_path
    assert usage["actual_usage"]["observed_access"] == {
        "kind": "python_file_open_read",
        "workspace_relative_path": f"{model_path}/marker.txt",
    }
    jsonschema.validate(result, json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8")))


def test_explicit_python_probe_keeps_resource_unverified_when_program_does_not_open_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-not-opened", "model-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=["-c", "import sys; print(sys.argv[1])", "{{openair-resource:model}}"],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "consumption_probe": {"kind": "python_file_open_read"},
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )

    assert result["state"] == "succeeded"
    usage = result["resource_usage"][0]
    assert usage["verification"] == "unverified"
    assert "actual_usage" not in usage
    assert "consumption_evidence" not in usage


def test_explicit_python_probe_verifies_archive_derived_dataset_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    existing_code = tmp_path / "repository" / "SentEval" / "senteval"
    existing_code.mkdir(parents=True)
    (existing_code / "sts.py").write_text("# existing code\n", encoding="utf-8")
    relative_path, digest = _prepare_tar_resource(
        tmp_path,
        "dataset-probed-archive",
        [_tar_file("SentEval/data/downstream/sample.txt", b"dataset-probed-ok")],
    )
    result = adapter.run_exploratory_run(
        _request(
            executable,
            argv=[
                "-c",
                "import pathlib; print(pathlib.Path('SentEval/data/downstream/sample.txt').read_text())",
            ],
            resource_bindings=[
                {
                    "usage": "dataset",
                    "delivery": {"kind": "tar_extract_working_directory"},
                    "consumption_probe": {"kind": "python_file_open_read"},
                    "resource": _file_resource("dataset", relative_path, digest),
                }
            ],
        )
    )

    assert result["state"] == "succeeded"
    usage = result["resource_usage"][0]
    assert usage["verification"] == "verified"
    assert usage["actual_usage"]["workspace_relative_path"] == relative_path
    assert usage["actual_usage"]["observed_access"]["workspace_relative_path"] == "repository/SentEval/data/downstream/sample.txt"
    assert usage["consumption_evidence"]["accessed_workspace_relative_path"] == "repository/SentEval/data/downstream/sample.txt"
    assert not (tmp_path / "repository" / "SentEval" / "data" / "downstream" / "sample.txt").exists()
    assert (existing_code / "sts.py").is_file()
    jsonschema.validate(result, json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8")))


def test_reserved_python_probe_config_environment_cannot_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _prepare_workspace(tmp_path, monkeypatch)
    model_path = _prepare_resource(tmp_path, "model-probe-env", "model-ok")
    result = adapter.run_exploratory_run(
        _request(
            executable,
            environment_variables={"OPENAIR_RESOURCE_ACCESS_PROBE_CONFIG": "spoofed"},
            argv=["-c", "print('must-not-run')", "{{openair-resource:model}}"],
            resource_bindings=[
                {
                    "usage": "model",
                    "argv_index": 2,
                    "consumption_probe": {"kind": "python_file_open_read"},
                    "resource": _formal_resource("model", model_path),
                }
            ],
        )
    )
    assert result["failure_code"] == "skill_input_invalid"

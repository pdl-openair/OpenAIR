from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest
import yaml

from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.python_adapter_skills import (
    PYTHON_ADAPTER_ENTRYPOINTS,
    resolve_python_adapter_skill,
)
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    resolve_registered_skill_input,
    validate_registered_skill_input_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "cs_research" / "repository-revision-acquisition"


def _registry_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8"))["skills"]
    return next(row for row in rows if row["skill_id"] == "repository_revision_acquisition")


def _binding_row() -> dict:
    rows = yaml.safe_load((REPO_ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8"))["bindings"]
    return next(
        row
        for row in rows
        if row["bindingId"] == "bind:sdr:reproducibility:skill:repository_revision_acquisition"
    )


def test_registry_binding_and_python_adapter_use_formal_contract() -> None:
    registry = _registry_row()
    assert registry["stages"] == ["solution_design"]
    assert {"reproducibility", "code_identity_acquisition"}.issubset(
        set(registry["substages"])
    )
    assert {"sdr", "cs_reproduction"}.issubset(set(registry["packs"]))
    assert registry["runtime"] == "python_adapter"
    assert registry["runtime_contract"] == {
        "kind": "external_adapter",
        "requires_external_action": True,
        "adapter_id": "python_adapter:repository_revision_acquisition",
        "evidence_contract_version": "external-action-evidence/v1",
    }
    assert resolve_python_adapter_skill("repository_revision_acquisition") == (
        "repository_revision_acquisition",
        "skills/cs_research/repository-revision-acquisition",
    )
    assert PYTHON_ADAPTER_ENTRYPOINTS["python_adapter:repository_revision_acquisition"] == (
        "adapter.repository_revision_adapter",
        "run_repository_revision_acquisition",
        "payload_v1",
    )

    binding = _binding_row()["inputSources"]
    fields = {row["targetField"]: row for row in binding["fields"]}
    assert binding["contractVersion"] == 2
    assert set(fields) == {"repository", "revision"}
    assert all(row["sourcePresence"] == "required" for row in fields.values())
    assert all(row["source"] == {"kind": "scientificState", "field": name} for name, row in fields.items())


def _create_bare_source(tmp_path: Path) -> tuple[Path, str]:
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=work, check=True, capture_output=True)
    (work / "payload.txt").write_text("fixed", encoding="utf-8")
    subprocess.run(["git", "add", "payload.txt"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=OpenAIR Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixed"],
        cwd=work,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "tag", "v0.4.0"], cwd=work, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, text=True, capture_output=True
    ).stdout.strip()
    daemon_root = tmp_path / "daemon"
    daemon_root.mkdir()
    bare = daemon_root / "source.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return daemon_root, commit


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("local git daemon did not become ready")


def test_registered_runner_performs_real_git_clone_checkout_and_head_verification(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    daemon_root, commit = _create_bare_source(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    port = _free_local_port()
    proc = subprocess.Popen(
        [
            "git",
            "daemon",
            "--reuseaddr",
            "--export-all",
            f"--base-path={daemon_root}",
            "--listen=127.0.0.1",
            f"--port={port}",
            str(daemon_root),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        result = run_python_skill_adapter(
            repo_root=REPO_ROOT,
            skill_id="repository_revision_acquisition",
            skill_path="skills/cs_research/repository-revision-acquisition",
            adapter_id="python_adapter:repository_revision_acquisition",
            input_payload={
                "repository": f"git://127.0.0.1:{port}/source.git",
                "revision": "v0.4.0",
            },
            execution_cwd=workspace,
            environment_overrides={"OPENAIR_ADAPTER_EXECUTION_CWD": str(workspace.resolve())},
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    assert result["state"] == "succeeded"
    assert result["resolved_commit_sha"] == commit
    assert result["verified_head"] == commit
    actual_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace / "repository",
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert actual_head == commit


def _minimal_frozen_snapshot() -> tuple[dict, dict]:
    input_schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    registry = _registry_row()
    binding = _binding_row()
    frozen_binding = {**binding, "enabled": True}
    snapshot = {
        "definitions": {
            "skills": [{
                "skillId": "repository_revision_acquisition",
                "definitionVersion": 1,
                "sourceContract": {
                    **registry,
                    "frozenSchemas": {"input": {"sha256": "0" * 64, "schema": input_schema}},
                },
            }],
            "agents": [],
        },
        "bindings": {"skills": [frozen_binding]},
    }
    return snapshot, frozen_binding


def test_formal_inputs_freeze_exact_values_and_missing_revision_fails_closed() -> None:
    snapshot, binding = _minimal_frozen_snapshot()
    request = SkillInputRequest(
        task_id="task-repository",
        strategy="direct_skill",
        skill_id="repository_revision_acquisition",
        definition_version=1,
    )
    scientific_state = {
        "repository": "https://github.com/example/project.git",
        "revision": "v0.4.0",
    }
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=request,
        input_context={"scientificStatePresent": True, "scientificState": scientific_state},
        skill_binding=binding,
    )
    assert resolved == scientific_state
    assert validate_registered_skill_input_snapshot(
        snapshot=snapshot,
        request=request,
        resolved_input=resolved,
        skill_binding=binding,
    ) == scientific_state

    with pytest.raises(InputContractGateFailure) as caught:
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=request,
            input_context={
                "scientificStatePresent": True,
                "scientificState": {"repository": scientific_state["repository"]},
            },
            skill_binding=binding,
        )
    assert caught.value.code == "input_composition_source_field_missing"
    assert caught.value.field_paths == ("revision",)

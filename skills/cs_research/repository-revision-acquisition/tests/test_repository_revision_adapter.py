from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import time
from typing import Any

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from adapter import repository_revision_adapter as adapter  # noqa: E402


def _commit(repo: Path, message: str) -> str:
    (repo / "payload.txt").write_text(message, encoding="utf-8")
    subprocess.run(["git", "add", "payload.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=OpenAIR Test", "-c", "user.email=test@example.invalid", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _local_source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=source, check=True, capture_output=True)
    commit = _commit(source, "first")
    subprocess.run(["git", "tag", "v0.4.0"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "branch", "feature"], cwd=source, check=True, capture_output=True)
    return source, commit


def _route_clone_to_local_source(
    monkeypatch: pytest.MonkeyPatch, source: Path
) -> None:
    original = adapter._run_git

    def route_local_clone(
        executable: str, args: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["clone", "--no-checkout"]:
            env = adapter._git_environment()
            env["GIT_ALLOW_PROTOCOL"] = "file"
            return subprocess.run(
                [executable, "clone", "--no-checkout", "--", str(source), "repository"],
                cwd=str(cwd),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                shell=False,
                timeout=30,
            )
        return original(executable, args, cwd=cwd)

    monkeypatch.setattr(adapter, "_run_git", route_local_clone)


def test_run_git_preserves_stdout_stderr_and_returncode(tmp_path: Path) -> None:
    result = adapter._run_git(
        sys.executable,
        [
            "-c",
            "import sys; print('captured-out'); print('captured-err', file=sys.stderr); raise SystemExit(7)",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 7
    assert result.stdout.strip() == "captured-out"
    assert result.stderr.strip() == "captured-err"


def test_real_local_git_success_preserves_tag_checkout_and_head_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git unavailable")
    source, commit = _local_source(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _route_clone_to_local_source(monkeypatch, source)
    result = adapter._acquire_repository_in_workspace(
        "https://example.invalid/setfit", "v0.4.0", workspace=workspace
    )

    assert result["state"] == "succeeded"
    assert result["resolved_commit_sha"] == commit
    assert result["verified_head"] == commit
    assert result["workspace_relative_path"] == "repository"
    assert [item["operation"] for item in result["execution_evidence"]["operations"]] == [
        "clone", "resolve_revision", "checkout", "read_head"
    ]


def test_source_evidence_is_preserved_only_when_it_matches_repository_identity() -> None:
    evidence = {
        "kind": "declared_source_page_code_link",
        "source_url": "https://arxiv.org/abs/2201.00001",
        "repository_url": "https://github.com/example/project.git",
        "selection_basis": "declared_page_direct_code_link",
    }

    assert adapter._validated_input({
        "repository": "https://github.com/example/project",
        "revision": "HEAD",
        "source_evidence": evidence,
    }) == (
        "https://github.com/example/project",
        "HEAD",
        evidence,
    )
    assert adapter._validated_input({
        "repository": "https://github.com/other/project",
        "revision": "HEAD",
        "source_evidence": evidence,
    }) is None


@pytest.mark.parametrize(
    "content_form", ["metadata_only", "landing_page", "html", "pdf", "xml", "tei"]
)
def test_local_acquisition_preserves_optional_source_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_form: str
) -> None:
    import jsonschema

    source, commit = _local_source(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _route_clone_to_local_source(monkeypatch, source)
    evidence = {
        "kind": "primary_paper_author_declared_code_link",
        "source_url": "https://arxiv.org/abs/2104.08821v4",
        "repository_url": "https://github.com/princeton-nlp/simcse.git",
        "selection_basis": "unique_author_declared_code_link_in_primary_paper",
        "content_form": content_form,
        "paper_identity": {"arxiv_id": "2104.08821v4", "title": "SimCSE"},
    }
    payload = {
        "repository": evidence["repository_url"],
        "revision": "v0.4.0",
        "source_evidence": evidence,
    }
    assert adapter._validated_input(payload) == (
        payload["repository"],
        payload["revision"],
        evidence,
    )
    result = adapter._acquire_repository_in_workspace(
        payload["repository"], payload["revision"], workspace=workspace, source_evidence=evidence
    )
    assert result["state"] == "succeeded"
    assert result["resolved_commit_sha"] == result["verified_head"] == commit
    assert result["source_evidence"] == evidence
    schema = json.loads((SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)


def test_real_local_git_remote_branch_resolution_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git unavailable")
    source, commit = _local_source(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _route_clone_to_local_source(monkeypatch, source)
    result = adapter._acquire_repository_in_workspace(
        "https://example.invalid/setfit", "feature", workspace=workspace
    )
    assert result["state"] == "succeeded"
    assert result["resolved_commit_sha"] == commit
    assert result["verified_head"] == commit


def test_accessible_repository_rejects_unresolvable_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Downstream acquisition owns revision validity after source resolution passes."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git unavailable")
    source, _commit = _local_source(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _route_clone_to_local_source(monkeypatch, source)
    result = adapter._acquire_repository_in_workspace(
        "https://example.invalid/setfit",
        "definitely-not-a-real-revision-zzz",
        workspace=workspace,
    )
    assert result["state"] == "failed"
    assert result["failure_code"] == "revision_unresolvable"
    assert result["failed_stage"] == "resolve_revision"
    assert not (workspace / "repository").exists()


def test_ordinary_clone_failure_keeps_failure_and_cleanup_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fake_run(executable: str, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if args == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "git version test", "")
        target = cwd / "repository"
        target.mkdir()
        (target / "partial.pack").write_bytes(b"partial")
        return subprocess.CompletedProcess(args, 128, "", "clone failed")

    monkeypatch.setattr(adapter.shutil, "which", lambda name: "git")
    monkeypatch.setattr(adapter, "_run_git", fake_run)
    result = adapter._acquire_repository_in_workspace(
        "https://example.invalid/repo", "v1", workspace=workspace
    )
    assert result == {
        "state": "failed",
        "failure_code": "repository_unavailable",
        "failed_stage": "clone",
        "git_exit_code": 128,
    }
    assert not (workspace / "repository").exists()


def _windows_tree_helper(tmp_path: Path) -> Path:
    helper = tmp_path / "windows_tree_helper.py"
    helper.write_text(
        textwrap.dedent(
            """
            import json
            import os
            from pathlib import Path
            import subprocess
            import sys
            import time

            pid_file = Path(sys.argv[1])
            target = Path(sys.argv[2])
            target.mkdir(parents=True, exist_ok=True)
            (target / "partial.pack").write_bytes(b"partial")
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
            ready_file = pid_file.with_suffix(".ready")
            ready_file.write_text(
                json.dumps({"parent": os.getpid(), "child": child.pid}), encoding="utf-8"
            )
            ready_file.replace(pid_file)
            time.sleep(3600)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return helper


def _wait_for_windows_process_exit(pid: int, timeout_ms: int = 5000) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_invalid_parameter
    try:
        return kernel32.WaitForSingleObject(handle, timeout_ms) == wait_object_0
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows process-tree semantics")
def test_windows_timeout_kills_real_process_tree_and_cleans_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pid_file = tmp_path / "pids.json"
    helper = _windows_tree_helper(tmp_path)
    original = adapter._run_git
    from adapter import windows_process

    original_resume = windows_process._resume_suspended_process

    def resume_and_observe_real_tree(pid: int) -> None:
        original_resume(pid)
        ready_deadline = time.monotonic() + 5.0
        while not pid_file.is_file() and time.monotonic() < ready_deadline:
            time.sleep(0.01)
        assert pid_file.is_file(), "real parent and child must start before testing tree cleanup"

    # Observe real readiness before communicate checks the original timeout.
    # The production deadline and real Job Object termination remain unchanged.
    monkeypatch.setattr(windows_process, "_resume_suspended_process", resume_and_observe_real_tree)

    def run_with_real_timeout(executable: str, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        if args == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "git version test", "")
        if args[:2] == ["clone", "--no-checkout"]:
            return original(
                sys.executable,
                [str(helper), str(pid_file), str(cwd / "repository")],
                cwd=cwd,
            )
        raise AssertionError(f"unexpected Git call: {args}")

    monkeypatch.setattr(adapter.shutil, "which", lambda name: "git")
    monkeypatch.setattr(adapter, "_GIT_TIMEOUT_SECONDS", 0.75)
    monkeypatch.setattr(adapter, "_run_git", run_with_real_timeout)

    started = time.monotonic()
    result = adapter._acquire_repository_in_workspace(
        "https://example.invalid/repo", "v1", workspace=workspace
    )
    elapsed = time.monotonic() - started

    assert result == {
        "state": "failed",
        "failure_code": "repository_unavailable",
        "failed_stage": "clone",
    }
    # Includes the bounded readiness observation and production reap allowance.
    assert elapsed < 12.0
    assert pid_file.is_file(), "helper must report both real process IDs before timeout"
    pids = json.loads(pid_file.read_text(encoding="utf-8"))
    assert _wait_for_windows_process_exit(pids["parent"])
    assert _wait_for_windows_process_exit(pids["child"])
    assert not (workspace / "repository").exists()

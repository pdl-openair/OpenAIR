"""Canary: platform consistency gate fires on CRLF and stays green on LF."""

from __future__ import annotations

from pathlib import Path

from conftest import commit, make_repo, run_gate


def _repo_with(path: Path, payload: bytes) -> Path:
    repo = make_repo(path)
    (repo / "file.txt").write_bytes(payload)
    commit(repo, "fixture", ["file.txt"])
    return repo


def test_crlf_is_red(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path / "sick", b"line\r\n")
    proc = run_gate("check_platform_consistency.py", "--root", str(repo), "--all")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "CRLF" in proc.stdout


def test_lf_is_green(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path / "clean", b"line\n")
    proc = run_gate("check_platform_consistency.py", "--root", str(repo), "--all")
    assert proc.returncode == 0, proc.stdout + proc.stderr

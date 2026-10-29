"""Shared fixtures for the gate script unit tests.

Adds scripts/gates/ to sys.path so tests can import the gate modules directly
(they are standalone stdlib scripts, not a package).
"""
import os
import subprocess
import sys

GATES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GATES_DIR not in sys.path:
    sys.path.insert(0, GATES_DIR)

import pytest  # noqa: E402


def run_git(repo: str, *args: str) -> str:
    """Run git in the repo and return stdout; raises AssertionError on error."""
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def git_repo(tmp_path):
    """A git repo with one base commit; returns (repo_path_str, base_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(str(repo), "init", "-q", "-b", "main")
    run_git(str(repo), "config", "user.email", "gates@example.invalid")
    run_git(str(repo), "config", "user.name", "Gate Tests")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    run_git(str(repo), "add", "README.md")
    run_git(str(repo), "commit", "-q", "-m", "base")
    base = run_git(str(repo), "rev-parse", "HEAD").strip()
    return str(repo), base

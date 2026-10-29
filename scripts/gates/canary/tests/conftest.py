"""Shared fixtures for the gate canary suite (SPEC-GATE-01, design §7.4).

Every canary builds a throwaway git repository under pytest's ``tmp_path``
and drives the REAL gate script as a subprocess, asserting both directions:
a sick sample must exit red and a clean sample must exit green. No real
repository file is ever touched.

Requirement: the interpreter running pytest must be able to run
``python -m ruff`` and ``python -m mypy`` (the CI gate-canary job installs
them via ``uv sync --locked --project services/api``).
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

CANARY_ROOT = Path(__file__).resolve().parents[4]
GATES_DIR = CANARY_ROOT / "scripts" / "gates"


def run_git(repo: Path, *args: str) -> str:
    """Run git inside the fixture repo; raise AssertionError on failure."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


def make_repo(path: Path) -> Path:
    """Create and return an initialized empty git repository at ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", "main")
    run_git(path, "config", "user.email", "canary@example.invalid")
    run_git(path, "config", "user.name", "Gate Canary")
    return path


def commit(repo: Path, message: str, paths: Sequence[str]) -> None:
    """Stage exactly ``paths`` and commit them with the canary identity."""
    assert paths, "canary commit needs explicit paths"
    run_git(repo, "add", "--", *paths)
    run_git(repo, "commit", "-q", "-m", message)


def run_gate(script: str, *args: str, cwd: Path | None = None):
    """Run a real gate script with the current interpreter."""
    return subprocess.run(
        [sys.executable, str(GATES_DIR / script), *args],
        cwd=str(cwd or CANARY_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def write_lines(path: Path, count: int, prefix: str = "line") -> None:
    """Write exactly ``count`` newline-terminated lines to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{prefix} {i}\n" for i in range(count)), encoding="utf-8")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A fresh empty git repository for one canary case."""
    return make_repo(tmp_path / "repo")

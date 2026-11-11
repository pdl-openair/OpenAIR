"""Unit tests for check_platform_consistency: line endings, case, modes, bash."""

import os
from pathlib import Path

import check_platform_consistency as platform
from conftest import run_git


def _commit(repo: Path, *paths: str) -> None:
    run_git(str(repo), "add", "--", *paths)
    run_git(str(repo), "commit", "-q", "-m", "fixture")


def test_crlf_blocks_but_windows_suffix_is_allowed(tmp_path: Path, git_repo) -> None:
    repo = Path(git_repo[0])
    (repo / "bad.md").write_bytes(b"line\r\n")
    (repo / "ok.bat").write_bytes(b"line\r\n")
    _commit(repo, "bad.md", "ok.bat")
    problems = platform.run_checks(repo, None, True)
    assert any("CRLF" in p and "bad.md" in p for p in problems)
    assert not any("ok.bat" in p for p in problems)


def test_case_collision_is_detected(tmp_path: Path, git_repo) -> None:
    repo = Path(git_repo[0])
    (repo / "Readme.md").write_text("a\n", encoding="utf-8")
    (repo / "README.md").write_text("b\n", encoding="utf-8")
    _commit(repo, "Readme.md", "README.md")
    problems = platform.run_checks(repo, None, True)
    assert any("case collision" in p for p in problems)


def test_shell_scripts_must_be_executable(tmp_path: Path, git_repo) -> None:
    repo = Path(git_repo[0])
    script = repo / "run.sh"
    script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    os.chmod(script, 0o644)
    _commit(repo, "run.sh")
    problems = platform.run_checks(repo, None, True)
    assert any("not executable" in p and "run.sh" in p for p in problems)


def test_bash_syntax_error_is_detected(tmp_path: Path, git_repo) -> None:
    repo = Path(git_repo[0])
    script = repo / "bad.sh"
    script.write_text("if true; then\n", encoding="utf-8")
    os.chmod(script, 0o755)
    _commit(repo, "bad.sh")
    problems = platform.run_checks(repo, None, True)
    assert any("bash syntax" in p for p in problems)


def test_base_limits_scope_to_changed_files(tmp_path: Path, git_repo) -> None:
    repo = Path(git_repo[0])
    (repo / "legacy.md").write_bytes(b"legacy\r\n")
    _commit(repo, "legacy.md")
    base = run_git(str(repo), "rev-parse", "HEAD").strip()
    (repo / "new.md").write_text("clean\n", encoding="utf-8")
    _commit(repo, "new.md")
    problems = platform.run_checks(repo, base, False)
    assert not any("legacy.md" in p for p in problems)

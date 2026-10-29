"""Canary ⑦⑧: the incremental diff gate (SPEC-CODE-01 / SPEC-COV-01).

Red-green contract (design §7.4):
  ⑦ ruff mode reports a violation on an ADDED line (exit 1) while the same
     violation sitting only on base-existing lines is filtered out (exit 0)
  ⑧ coverage mode blocks when added product lines are <50% covered (exit 1)
     and passes at >=50% (exit 0)

The ruff canary runs the real ``ruff`` through ``diff_gate --python``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from conftest import commit, make_repo, run_gate, run_git

RUFF_TOML = 'line-length = 30\n\n[lint]\nselect = ["E501"]\n'
BASE_LONG_LINE = 'base = "this base line deliberately exceeds thirty characters"\n'
ADDED_LONG_LINE = 'grown = "this newly added line exceeds thirty characters too"\n'
ADDED_SHORT_LINE = 'grown = "short"\n'


def _seed_ruff_repo(repo: Path, added_line: str) -> None:
    (repo / "ruff.toml").write_text(RUFF_TOML, encoding="utf-8")
    (repo / "mod.py").write_text(BASE_LONG_LINE, encoding="utf-8")
    commit(repo, "base state", ["ruff.toml", "mod.py"])
    run_git(repo, "tag", "base-point")
    with (repo / "mod.py").open("a", encoding="utf-8") as handle:
        handle.write(added_line)
    commit(repo, "head state", ["mod.py"])


def _run_ruff(repo: Path):
    return run_gate(
        "diff_gate.py",
        "--ruff",
        "--root",
        str(repo),
        "--base",
        "base-point",
        "--python",
        sys.executable,
        cwd=repo,
    )


def test_canary_ruff_violation_on_added_line_fails(tmp_path: Path) -> None:
    sick = make_repo(tmp_path / "sick")
    _seed_ruff_repo(sick, ADDED_LONG_LINE)
    proc = _run_ruff(sick)
    assert proc.returncode == 1, f"expected FAIL, got:\n{proc.stdout}\n{proc.stderr}"
    assert "1 violation(s) on added lines" in proc.stdout
    assert "E501" in proc.stdout

    clean = make_repo(tmp_path / "clean")
    _seed_ruff_repo(clean, ADDED_SHORT_LINE)
    proc = _run_ruff(clean)
    assert proc.returncode == 0, f"expected PASS, got:\n{proc.stdout}\n{proc.stderr}"
    # The base-existing violation is still reported by ruff but filtered out.
    assert "0 violation(s) on added lines" in proc.stdout
    assert "1 total, filtered" in proc.stdout


def _seed_coverage_repo(repo: Path) -> None:
    module = repo / "services" / "api" / "app" / "mod.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("".join(f"v{i} = {i}\n" for i in range(10)), encoding="utf-8")
    commit(repo, "base state", ["services/api/app/mod.py"])
    run_git(repo, "tag", "base-point")
    with module.open("a", encoding="utf-8") as handle:
        handle.write("".join(f"v{i} = {i}\n" for i in range(10, 20)))
    commit(repo, "head state", ["services/api/app/mod.py"])


def _coverage_file(repo: Path, covered_added: list[int]) -> Path:
    added = list(range(11, 21))
    payload = {
        "files": {
            "services/api/app/mod.py": {
                "executed_lines": list(range(1, 11)) + covered_added,
                "missing_lines": [n for n in added if n not in covered_added],
            }
        }
    }
    path = repo / "coverage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_coverage(repo: Path, coverage_file: Path):
    return run_gate(
        "diff_gate.py",
        "--coverage",
        "--root",
        str(repo),
        "--base",
        "base-point",
        "--coverage-file",
        str(coverage_file),
        cwd=repo,
    )


def test_canary_diff_coverage_below_threshold_fails(tmp_path: Path) -> None:
    sick = make_repo(tmp_path / "sick")
    _seed_coverage_repo(sick)
    proc = _run_coverage(sick, _coverage_file(sick, covered_added=[11, 12, 13]))
    assert proc.returncode == 1, f"expected FAIL, got:\n{proc.stdout}\n{proc.stderr}"
    assert "30.0%" in proc.stdout
    assert "below threshold" in proc.stderr

    clean = make_repo(tmp_path / "clean")
    _seed_coverage_repo(clean)
    proc = _run_coverage(clean, _coverage_file(clean, covered_added=list(range(11, 17))))
    assert proc.returncode == 0, f"expected PASS, got:\n{proc.stdout}\n{proc.stderr}"
    assert "60.0%" in proc.stdout

"""Canary ①②③④: the oversized-module placement guard (SPEC-PLACE-01).

Red-green contract (design §7.4):
  ① a >800-line module that does not shrink  -> placement_guard exits 1
  ② a >800-line module that DOES shrink      -> exits 0 (guard rewards paydown)
  ③ a module at <=800 lines at the base      -> exits 0 even when it grows
  ④ an exemption entry neutralizes case ①    -> exits 0
"""

from __future__ import annotations

from pathlib import Path

from conftest import commit, run_gate, run_git, write_lines

EXEMPTIONS_REL = "spec/enforcement/placement-exemptions.yml"
EMPTY_EXEMPTIONS = "exemptions: []\n"
EXEMPT_EXEMPTIONS = (
    "exemptions:\n"
    "  - path: big.py\n"
    "    reason: canary fixture exemption\n"
    "    review-date: 2099-01-01\n"
    "    added-by: Gate Canary\n"
)


def _seed(repo: Path, base_lines: int, head_lines: int, exemptions: str) -> None:
    """Commit an 800-ish ``big.py`` at a tagged base, then change its size."""
    exemptions_path = repo / EXEMPTIONS_REL
    exemptions_path.parent.mkdir(parents=True, exist_ok=True)
    exemptions_path.write_text(exemptions, encoding="utf-8")
    write_lines(repo / "big.py", base_lines)
    commit(repo, "base state", ["big.py", EXEMPTIONS_REL])
    run_git(repo, "tag", "base-point")
    write_lines(repo / "big.py", head_lines)
    commit(repo, "head state", ["big.py"])


def _run(repo: Path):
    return run_gate(
        "placement_guard.py",
        "--root",
        str(repo),
        "--base",
        "base-point",
        cwd=repo,
    )


def test_canary_oversized_module_growth_is_blocked(git_repo: Path) -> None:
    _seed(git_repo, base_lines=801, head_lines=810, exemptions=EMPTY_EXEMPTIONS)
    proc = _run(git_repo)
    assert proc.returncode == 1, f"expected BLOCKED, got:\n{proc.stdout}\n{proc.stderr}"
    assert "BLOCKED" in proc.stderr
    assert "big.py" in proc.stderr


def test_canary_oversized_module_shrink_is_allowed(git_repo: Path) -> None:
    _seed(git_repo, base_lines=801, head_lines=790, exemptions=EMPTY_EXEMPTIONS)
    proc = _run(git_repo)
    assert proc.returncode == 0, f"expected pass, got:\n{proc.stdout}\n{proc.stderr}"
    assert "0 violation(s)" in proc.stdout


def test_canary_small_module_growth_is_allowed(git_repo: Path) -> None:
    _seed(git_repo, base_lines=790, head_lines=798, exemptions=EMPTY_EXEMPTIONS)
    proc = _run(git_repo)
    assert proc.returncode == 0, f"expected pass, got:\n{proc.stdout}\n{proc.stderr}"
    assert "0 violation(s)" in proc.stdout


def test_canary_exemption_entry_neutralizes_block(git_repo: Path) -> None:
    _seed(git_repo, base_lines=801, head_lines=810, exemptions=EXEMPT_EXEMPTIONS)
    proc = _run(git_repo)
    assert proc.returncode == 0, f"expected pass, got:\n{proc.stdout}\n{proc.stderr}"
    assert "exemption active: big.py" in proc.stdout
    assert "0 violation(s)" in proc.stdout

"""Canary ⑤⑥: the mypy scope ratchet gate (SPEC-TYPE-01).

Red-green contract (design §7.4):
  ⑤ a NEW out-of-scope-list mypy error is blocked  -> mypy_gate exits 1
     (clean sibling repo with no errors exits 0)
  ⑥ ADDING an entry to the exemption list is blocked by the ratchet
     (leaving the list unchanged exits 0)

Runs the real ``mypy`` through ``mypy_gate.py --python sys.executable``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import commit, make_repo, run_gate, run_git

SCOPE_REL = "spec/enforcement/mypy-scope.txt"
MYPY_INI = "[mypy]\nignore_missing_imports = True\n"


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed(repo: Path, scope_text: str, module_name: str, module_text: str) -> None:
    """Commit mypy.ini + one app module + the scope file, then tag the base."""
    _write(repo, "mypy.ini", MYPY_INI)
    _write(repo, f"services/api/app/{module_name}", module_text)
    _write(repo, SCOPE_REL, scope_text)
    commit(
        repo,
        "base state",
        ["mypy.ini", f"services/api/app/{module_name}", SCOPE_REL],
    )
    run_git(repo, "tag", "base-point")


def _run(repo: Path):
    return run_gate(
        "mypy_gate.py",
        "--root",
        str(repo),
        "--base",
        "base-point",
        "--python",
        sys.executable,
        cwd=repo,
    )


def test_canary_new_out_of_scope_mypy_error_is_blocked(tmp_path: Path) -> None:
    sick = make_repo(tmp_path / "sick")
    _seed(
        sick,
        scope_text="# empty scope: every in-scope file must be clean\n",
        module_name="bad.py",
        module_text='def answer() -> int:\n    return "forty-two"\n',
    )
    proc = _run(sick)
    assert proc.returncode == 1, f"expected FAIL, got:\n{proc.stdout}\n{proc.stderr}"
    assert "out-of-scope mypy error" in proc.stderr

    clean = make_repo(tmp_path / "clean")
    _seed(
        clean,
        scope_text="# empty scope: every in-scope file must be clean\n",
        module_name="fine.py",
        module_text="def answer() -> int:\n    return 42\n",
    )
    proc = _run(clean)
    assert proc.returncode == 0, f"expected PASS, got:\n{proc.stdout}\n{proc.stderr}"
    assert "[mypy_gate] PASS" in proc.stdout


def test_canary_scope_list_addition_is_ratchet_blocked(tmp_path: Path) -> None:
    sick = make_repo(tmp_path / "sick")
    _seed(
        sick,
        scope_text="services/api/app/a.py\n",
        module_name="a.py",
        module_text="# canary module a\n",
    )
    # Both listed modules must exist on disk (zombie check), both stay clean.
    _write(sick, "services/api/app/b.py", "# canary module b\n")
    _write(sick, SCOPE_REL, "services/api/app/a.py\nservices/api/app/b.py\n")
    commit(sick, "head state", ["services/api/app/b.py", SCOPE_REL])
    proc = _run(sick)
    assert proc.returncode == 1, f"expected FAIL, got:\n{proc.stdout}\n{proc.stderr}"
    assert "ratchet violation" in proc.stderr
    assert "services/api/app/b.py" in proc.stderr

    clean = make_repo(tmp_path / "clean")
    _seed(
        clean,
        scope_text="services/api/app/a.py\n",
        module_name="a.py",
        module_text="# canary module a\n",
    )
    proc = _run(clean)
    assert proc.returncode == 0, f"expected PASS, got:\n{proc.stdout}\n{proc.stderr}"
    assert "[mypy_gate] PASS" in proc.stdout

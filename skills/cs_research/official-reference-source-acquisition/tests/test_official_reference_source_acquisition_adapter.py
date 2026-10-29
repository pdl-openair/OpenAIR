from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "adapter" / "official_reference_source_acquisition_adapter.py"
spec = importlib.util.spec_from_file_location("official_reference_source_acquisition_adapter", MODULE)
adapter = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(adapter)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _fixed_checkout(workspace: Path) -> tuple[Path, str, dict]:
    repository = workspace / "repository"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.email", "test@example.com", cwd=repository)
    _git("config", "user.name", "OpenAIR Test", cwd=repository)
    (repository / "README.md").write_text("# Official\n\nMetric | Value\n--- | ---\nscore | 76.25\n", encoding="utf-8")
    (repository / "script.py").write_text("raise SystemExit('must never execute')\n", encoding="utf-8")
    _git("add", "README.md", "script.py", cwd=repository)
    _git("commit", "-m", "fixture", cwd=repository)
    commit = _git("rev-parse", "HEAD", cwd=repository)
    code_source = {
        "state": "succeeded",
        "repository": "https://example.test/official/repo.git",
        "requested_revision": "main",
        "resolved_commit_sha": commit,
        "verified_head": commit,
        "workspace_relative_path": "repository",
        "execution_evidence": {"kept": "upstream-only"},
    }
    return repository, commit, code_source


def test_read_reuses_fixed_checkout_and_preserves_source_identity(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _repository, commit, code_source = _fixed_checkout(workspace)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(workspace.resolve()))

    result = adapter.run_official_reference_source_acquisition({"code_source": code_source})

    assert result["state"] == "succeeded"
    assert result["source_kind"] == "official_repository"
    assert result["resolved_commit_sha"] == commit
    assert result["read_evidence"]["verified_head"] == commit
    assert result["read_evidence"]["head_matches_upstream_artifact"] is True
    assert [row["path"] for row in result["documents"]] == ["README.md"]
    assert result["documents"][0]["content"].endswith("score | 76.25\n")
    assert result["documents"][0]["document_id"].startswith("refdoc:")


def test_head_change_after_w1_fails_closed(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository, _commit, code_source = _fixed_checkout(workspace)
    (repository / "README.md").write_text("changed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repository)
    _git("commit", "-m", "mutated", cwd=repository)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OPENAIR_ADAPTER_EXECUTION_CWD", str(workspace.resolve()))

    result = adapter.run_official_reference_source_acquisition({"code_source": code_source})

    assert result == {
        "state": "failed",
        "failure_code": "fixed_repository_identity_mismatch",
        "failed_stage": "verify_head",
    }


def test_public_input_requires_successful_upstream_code_identity() -> None:
    good = {
        "code_source": {
            "state": "succeeded",
            "repository": "https://example.test/repo.git",
            "requested_revision": "main",
            "resolved_commit_sha": "a" * 40,
            "verified_head": "a" * 40,
            "workspace_relative_path": "repository",
        }
    }
    assert adapter._validated_input(good) is not None
    bad = {"code_source": {**good["code_source"], "verified_head": "b" * 40}}
    assert adapter._validated_input(bad) is None


def test_tree_filter_is_structural_not_scientific_keyword_filter() -> None:
    raw = (
        b"100644 blob aaaaa 20\tREADME.md\n"
        b"100644 blob bbbbb 20\tdocs/metrics.rst\n"
        b"100644 blob ccccc 20\tsrc/model.py\n"
        b"100644 blob ddddd 70000\tdocs/huge.txt\n"
    )
    assert adapter._parse_tree_rows(raw) == [("README.md", 20), ("docs/metrics.rst", 20)]

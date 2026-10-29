"""Unit tests for the pointer integrity gate (extraction + judgment)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parents[1]
SCRIPT = GATE_DIR / "check_pointer_integrity.py"


def _load_gate(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch):
    """Load the gate module with REPO_ROOT pointed at a disposable tree."""
    spec = importlib.util.spec_from_file_location("check_pointer_integrity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pointer_integrity"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_repo)
    return module


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("# docs\n", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
    (tmp_path / "docs" / "中文 文档.md").write_text("# zh\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tool.py").write_text("# tool\n", encoding="utf-8")
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "README.md").write_text("# spec\n", encoding="utf-8")
    return _load_gate(tmp_path, monkeypatch)


class TestExtractLinkTargets:
    def test_plain_markdown_link(self, repo):
        assert list(repo.extract_link_targets("see [x](docs/guide.md)")) == ["docs/guide.md"]

    def test_relative_link_kept(self, repo):
        assert list(repo.extract_link_targets("[up](../README.md)")) == ["../README.md"]

    def test_external_link_ignored(self, repo):
        assert list(repo.extract_link_targets("[web](https://example.com/a.md)")) == []

    def test_mailto_ignored(self, repo):
        assert list(repo.extract_link_targets("[m](mailto:a@b.c)")) == []

    def test_pure_anchor_ignored(self, repo):
        assert list(repo.extract_link_targets("[s](#section)")) == []

    def test_anchor_stripped_from_path(self, repo):
        got = list(repo.extract_link_targets("[x](docs/guide.md#heading)"))
        assert got == ["docs/guide.md"]

    def test_percent_encoded_target_unquoted(self, repo):
        got = list(
            repo.extract_link_targets("[x](docs/%E4%B8%AD%E6%96%87%20%E6%96%87%E6%A1%A3.md)")
        )
        assert got == ["docs/中文 文档.md"]


class TestExtractBacktickPaths:
    def test_repo_prefixed_path(self, repo):
        assert list(repo.extract_backtick_paths("edit `docs/guide.md` now")) == ["docs/guide.md"]

    def test_non_prefixed_span_ignored(self, repo):
        assert list(repo.extract_backtick_paths("run `make check`")) == []

    def test_glob_pattern_ignored(self, repo):
        assert list(repo.extract_backtick_paths("see `docs/*.md`")) == []

    def test_placeholder_pattern_ignored(self, repo):
        assert list(repo.extract_backtick_paths("see `docs/<name>/README.md`")) == []

    def test_url_ignored(self, repo):
        assert list(repo.extract_backtick_paths("`https://example.com/docs/x.md`")) == []

    def test_command_span_checks_each_token(self, repo):
        # The path token exists; the trailing argument must not break it.
        got = list(repo.extract_backtick_paths("run `python scripts/tool.py check`"))
        assert got == ["scripts/tool.py"]

    def test_trailing_cjk_punctuation_stripped(self, repo):
        got = list(repo.extract_backtick_paths("见 `docs/guide.md`。"))
        assert got == ["docs/guide.md"]


class TestCheckPath:
    def test_existing_file(self, repo, tmp_path):
        assert repo.check_path(tmp_path / "docs", "guide.md")

    def test_existing_dir(self, repo, tmp_path):
        assert repo.check_path(tmp_path, "docs/")

    def test_missing_file(self, repo, tmp_path):
        assert not repo.check_path(tmp_path / "docs", "nope.md")

    def test_escape_above_repo_root(self, repo, tmp_path):
        assert not repo.check_path(tmp_path, "../../outside.md")

    def test_url_encoded_link_resolves(self, repo, tmp_path):
        target = next(
            iter(
                repo.extract_link_targets("[x](docs/%E4%B8%AD%E6%96%87%20%E6%96%87%E6%A1%A3.md)")
            )
        )
        assert repo.check_path(tmp_path, target)


class TestScanFile:
    def test_clean_file_has_no_findings(self, repo, tmp_path):
        f = tmp_path / "spec" / "README.md"
        f.write_text("ok [docs](../docs/README.md) and `docs/guide.md`\n", encoding="utf-8")
        assert repo.scan_file(f) == []

    def test_broken_link_reported_with_line(self, repo, tmp_path):
        f = tmp_path / "spec" / "README.md"
        f.write_text("line1\nbroken [x](docs/missing.md)\n", encoding="utf-8")
        findings = repo.scan_file(f)
        assert findings == [(2, "link", "docs/missing.md")]

    def test_broken_backtick_reported(self, repo, tmp_path):
        f = tmp_path / "spec" / "README.md"
        f.write_text("see `docs/missing.md`\n", encoding="utf-8")
        findings = repo.scan_file(f)
        assert findings == [(1, "backtick", "docs/missing.md")]

    def test_external_and_anchor_never_reported(self, repo, tmp_path):
        f = tmp_path / "spec" / "README.md"
        f.write_text("[a](https://x/y.md) [b](#z)\n", encoding="utf-8")
        assert repo.scan_file(f) == []


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    module = _load_gate(tmp_path, monkeypatch)
    (tmp_path / "spec").mkdir(exist_ok=True)
    # No scan targets exist besides spec/**, which is empty of text files.
    assert module.main([]) == 0
    (tmp_path / "spec" / "README.md").write_text(
        "broken [x](docs/missing.md)\n", encoding="utf-8"
    )
    assert module.main([]) == 1
    out = capsys.readouterr().out
    assert "broken link -> docs/missing.md" in out
    assert "1 broken pointer(s)" in out

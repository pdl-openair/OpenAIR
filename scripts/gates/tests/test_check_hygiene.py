"""check_hygiene 单测：release 阻断 + staleness 提醒（B3 规格 §2.5-9/10）。"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_hygiene as ch  # noqa: E402


class _FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


# ---------- release 模式 ----------


def test_release_with_status_passes(capsys):
    assert ch.main(["--mode", "release", "--files", "STATUS.md", "docs/x.md"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_release_with_changelog_passes():
    assert ch.main(["--mode", "release", "--files", "apps/web/src/a.tsx", "CHANGELOG.md"]) == 0


def test_release_without_status_or_changelog_fails(capsys):
    assert ch.main(["--mode", "release", "--files", "services/api/app/main.py", "docs/y.md"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "SPEC-HYGIENE-01" in out


def test_release_missing_file_source_fails(capsys):
    """release 模式既无 --files 也无 --from-env → 用法错误（退出 2）。"""
    assert ch.main(["--mode", "release"]) == 2
    assert "FAIL" in capsys.readouterr().out


def test_release_from_env(monkeypatch, capsys):
    monkeypatch.setenv("PR_FILES", "apps/web/src/b.tsx\nSTATUS.md,docs/z.md")
    assert ch.main(["--mode", "release", "--from-env"]) == 0


def test_release_from_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("PR_FILES", raising=False)
    assert ch.main(["--mode", "release", "--from-env"]) == 1


# ---------- staleness 模式 ----------


def _today():
    return _dt.date(2026, 9, 24)


def test_staleness_fresh_no_warning(monkeypatch):
    monkeypatch.setattr(ch, "_git", lambda args: _FakeCompleted("abc123 2026-09-20\n"))
    stale, detail = ch.check_staleness(today=_today())
    assert not stale
    assert "正常" in detail


def test_staleness_stale_with_product_commits_warns(monkeypatch):
    def fake_git(args):
        if args[:3] == ["log", "-1", "--format=%H %cs"]:
            return _FakeCompleted("abc123 2026-08-01\n")
        return _FakeCompleted("7\n")

    monkeypatch.setattr(ch, "_git", fake_git)
    stale, detail = ch.check_staleness(today=_today())
    assert stale
    assert "警告" in detail and "7" in detail and "SPEC-HYGIENE-01" in detail


def test_staleness_stale_without_product_commits_silent(monkeypatch):
    def fake_git(args):
        if args[:3] == ["log", "-1", "--format=%H %cs"]:
            return _FakeCompleted("abc123 2026-08-01\n")
        return _FakeCompleted("0\n")

    monkeypatch.setattr(ch, "_git", fake_git)
    stale, detail = ch.check_staleness(today=_today())
    assert not stale
    assert "无产品提交" in detail


def test_staleness_no_history_skips(monkeypatch):
    monkeypatch.setattr(ch, "_git", lambda args: _FakeCompleted("", returncode=0))
    stale, detail = ch.check_staleness(today=_today())
    assert not stale
    assert "跳过" in detail


def test_staleness_always_exit_zero(monkeypatch, capsys):
    def fake_git(args):
        if args[:3] == ["log", "-1", "--format=%H %cs"]:
            return _FakeCompleted("abc123 2026-01-01\n")
        return _FakeCompleted("99\n")

    monkeypatch.setattr(ch, "_git", fake_git)
    assert ch.main(["--mode", "staleness"]) == 0
    assert "警告" in capsys.readouterr().out


# ---------- 常量 ----------


def test_constants():
    assert ch.RELEASE_REQUIRED == ("STATUS.md", "CHANGELOG.md")
    assert ch.STALENESS_DAYS == 14
    assert ch.PRODUCT_PATHS == ("services", "apps", "packages", "skills")

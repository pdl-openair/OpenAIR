"""check_commit_msg 单测：格式/type/禁用词/泛化 summary/Co-Authored-By（B3 规格 §2.2-3）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_commit_msg as ccm  # noqa: E402


def _run_file(tmp_path, message):
    f = tmp_path / "msg.txt"
    f.write_text(message, encoding="utf-8")
    return ccm.main(["--file", str(f)])


# ---------- 合规路径 ----------


def test_good_message_with_coauthor(tmp_path, capsys):
    """D6 场景：标准格式 + Claude Code 尾注 → 退出 0。"""
    msg = (
        "feat(api): add task creation endpoint\n\n"
        "Co-Authored-By: Claude Code <noreply@anthropic.com>\n"
    )
    assert _run_file(tmp_path, msg) == 0
    assert "0 FAIL" in capsys.readouterr().out


def test_good_message_pure_human(tmp_path):
    assert _run_file(tmp_path, "fix(workbench): correct stage status display\n") == 0


def test_all_allowed_types_pass(tmp_path):
    for t in ccm.ALLOWED_TYPES:
        assert _run_file(tmp_path, f"{t}(api): do something concrete here\n") == 0, t


def test_stdin_mode(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("docs(skills): add replay test rules\n"))
    assert ccm.main([]) == 0
    assert "0 FAIL" in capsys.readouterr().out


class _FakeStdin:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


# ---------- 格式失败 ----------


def test_missing_scope_fails(tmp_path, capsys):
    assert _run_file(tmp_path, "feat: add something concrete\n") == 1
    assert "scope" in capsys.readouterr().out


def test_missing_colon_fails(tmp_path):
    assert _run_file(tmp_path, "feat(api) add endpoint for tasks\n") == 1


def test_empty_message_fails(tmp_path):
    assert _run_file(tmp_path, "\n") == 1


# ---------- type 失败 ----------


def test_forbidden_type_update(tmp_path, capsys):
    assert _run_file(tmp_path, "update(api): refresh endpoint payload\n") == 1
    out = capsys.readouterr().out
    assert "update" in out and "禁用" in out


def test_forbidden_type_wip_and_misc(tmp_path):
    assert _run_file(tmp_path, "wip(api): intermediate state dump\n") == 1
    assert _run_file(tmp_path, "misc(repo): tidy things around repo\n") == 1


def test_unknown_type_fails(tmp_path):
    assert _run_file(tmp_path, "improve(api): make endpoint faster now\n") == 1


def test_forbidden_table_matches_rules_doc():
    """禁用清单以 git_commit_rules.md 实际条文为准（含 change）。"""
    assert ccm.FORBIDDEN_TYPES == ("update", "modify", "change", "optimize", "misc", "wip")
    assert set(ccm.ALLOWED_TYPES) == {
        "feat", "fix", "docs", "refactor", "style", "test", "chore", "revert",
    }


# ---------- summary 失败 ----------


def test_generic_summary_fails(tmp_path, capsys):
    assert _run_file(tmp_path, "chore(repo): update\n") == 1
    assert "泛化" in capsys.readouterr().out


def test_too_short_summary_fails(tmp_path):
    assert _run_file(tmp_path, "fix(api): x\n") == 1


# ---------- Co-Authored-By ----------


def test_coauthor_malformed_fails(tmp_path, capsys):
    msg = "feat(api): add endpoint with tests\n\nCo-Authored-By: Claude Code\n"
    assert _run_file(tmp_path, msg) == 1
    assert "Co-Authored-By" in capsys.readouterr().out


def test_coauthor_any_domain_accepted(tmp_path):
    """不校验邮箱域名（多工具并存）。"""
    msg = (
        "feat(api): add endpoint with tests\n\n"
        "Co-authored-by: qoder <qoder@users.noreply.github.com>\n"
    )
    assert _run_file(tmp_path, msg) == 0


def test_require_coauthor_flag(tmp_path, capsys):
    assert _run_file(tmp_path, "feat(api): add endpoint with tests\n") == 0
    f = tmp_path / "msg2.txt"
    f.write_text("feat(api): add endpoint with tests\n", encoding="utf-8")
    assert ccm.main(["--file", str(f), "--require-coauthor"]) == 1
    assert "Co-Authored-By" in capsys.readouterr().out

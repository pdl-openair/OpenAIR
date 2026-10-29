"""hook_guard 单测：四硬拦每条 >=2 正反例、两软提示、规则表常量（B3 规格 §2.3-5/6）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hook_guard as hg  # noqa: E402


def _bash(command, event="PreToolUse"):
    payload = {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": command}}
    return hg.handle(payload)


def _path(tool, file_path, event="PreToolUse"):
    payload = {"hook_event_name": event, "tool_name": tool, "tool_input": {"file_path": file_path}}
    return hg.handle(payload)


def _blocked(out):
    return out is not None and out.get("decision") == "block"


# ---------- 硬拦 1：--no-verify / -n ----------


def test_no_verify_blocked_two_ways():
    assert _blocked(_bash('git commit --no-verify -m "x"'))  # DoD D3 原型
    assert _blocked(_bash("git commit -n -m rework stage"))


def test_no_verify_short_cluster_blocked():
    assert _blocked(_bash('git commit -nm "skip hooks"'))


def test_normal_commit_not_blocked(monkeypatch):
    monkeypatch.setattr(hg, "_staged_stats", lambda cwd=None: None)
    assert _bash('git commit -m "feat(api): add endpoint"') is None


def test_unrelated_commands_not_blocked():
    assert _bash("git status") is None  # DoD D4 原型
    assert _bash("git log --oneline -5") is None


# ---------- 硬拦 2：git add -A / . ----------


def test_add_all_blocked_two_ways():
    assert _blocked(_bash("git add -A"))
    assert _blocked(_bash("git add ."))


def test_add_variants_blocked():
    assert _blocked(_bash("git add --all"))
    assert _blocked(_bash("git add ./"))
    assert _blocked(_bash("git -C repo add -A"))


def test_add_explicit_paths_not_blocked():
    assert _bash("git add scripts/gates/hook_guard.py") is None
    assert _bash("git add docs/README.md services/api/app/main.py") is None


# ---------- 硬拦 3：force push（一律拦；--force-with-lease 仅 batch/* 放行，D-9） ----------


def test_force_push_blocked_two_ways():
    assert _blocked(_bash("git push --force origin dev"))
    assert _blocked(_bash("git push -f origin dev"))


def test_force_with_lease_also_blocked():
    """非批次目标上的 --force-with-lease 仍拦（D-9 裁决后保留）。"""
    assert _blocked(_bash("git push --force-with-lease origin dev"))
    assert _blocked(_bash("git push --force-with-lease origin refactor/ai4se-harness"))


def test_force_with_lease_batch_branch_allowed():
    """D-9：lease 且目标全为 batch/* 战役批次分支（rebase 后受控同步）放行。"""
    assert _bash("git push --force-with-lease origin batch/b1-static-gates") is None
    assert (
        _bash(
            "git push --force-with-lease=refs/heads/batch/b1-static-gates:abc123 "
            "origin batch/b1-static-gates"
        )
        is None
    )
    # 管道后缀（tail/重定向）不属于 push 参数，不破坏 batch-only 判定
    assert (
        _bash("git push --force-with-lease origin batch/b1-static-gates 2>&1 | tail -2")
        is None
    )


def test_force_with_lease_piped_non_batch_blocked():
    """管道形态下非批次目标照拦。"""
    assert _blocked(_bash("git push --force-with-lease origin dev 2>&1 | tail -2"))


def test_force_with_lease_mixed_targets_blocked():
    """lease 但夹带非 batch 目标（batch 外还推 dev）拦。"""
    assert _blocked(_bash("git push --force-with-lease origin batch/b1-static-gates dev"))


def test_force_with_batch_still_blocked():
    """batch/* 目标上的裸 --force / -f / +refspec 不因 D-9 放行。"""
    assert _blocked(_bash("git push --force origin batch/b1-static-gates"))
    assert _blocked(_bash("git push origin +batch/b1-static-gates"))


def test_forced_refspec_blocked():
    assert _blocked(_bash("git push origin +dev:dev"))


def test_forced_refspec_without_colon_blocked():
    """F3①：`git push origin +dev`（无冒号，隐式强推）也拦。"""
    assert _blocked(_bash("git push origin +dev"))


def test_force_push_short_cluster_blocked():
    """F3②：短旗标簇含 f（-fq = force+quiet）拦。"""
    assert _blocked(_bash("git push -fq origin dev"))
    assert _blocked(_bash("git push -qf origin dev"))


def test_push_flags_without_f_not_blocked():
    """F3 反例：quiet/upstream/tags 等不含 f 的旗标放行。"""
    assert _bash("git push -q origin dev") is None
    assert _bash("git push -u origin feat/x") is None
    assert _bash("git push --tags origin dev") is None


def test_normal_push_not_blocked():
    assert _bash("git push origin dev") is None
    assert _bash("git push --set-upstream origin feat/x") is None


def test_compound_command_caught():
    assert _blocked(_bash("git add docs/x.md && git push -f origin dev"))


# ---------- H1：shell 分段（; && || | & 换行粘连 token 逃逸） ----------


def test_h1_semicolon_glued_token_blocked():
    """H1a/H1b：分隔符粘连进 token 的形态逐个拦。"""
    assert _blocked(_bash("git add -A; git status"))
    assert _blocked(_bash("git push -f;git status"))
    assert _blocked(_bash("git add -A&&git push -f origin dev"))
    assert _blocked(_bash("git add -A || git status"))
    assert _blocked(_bash("git add -A | grep staged"))
    assert _blocked(_bash("git add -A&git status"))
    assert _blocked(_bash("echo hi\ngit add -A"))


def test_h1_quoted_separators_not_split(monkeypatch):
    """引号内的分隔符不切段（软提示路径 monkeypatch 掉 staged 统计）。"""
    monkeypatch.setattr(hg, "_staged_stats", lambda cwd=None: None)
    assert _bash('git commit -m "a; b && c"') is None
    assert _bash("echo 'git add -A'") is None
    assert _bash('echo "| ; &&"') is None


def test_split_shell_segments_quote_aware():
    assert hg.split_shell_segments("a 'b;c' && d") == ["a 'b;c'", "d"]
    assert hg.split_shell_segments("x\ny") == ["x", "y"]
    assert hg.split_shell_segments("git status") == ["git status"]
    assert hg.split_shell_segments("") == []
    # 2>&1 被 & 切开属预期：重定向碎片随段自然归属，不影响 push 参数判定
    assert hg.split_shell_segments("p 2>&1 | t") == ["p 2>", "1", "t"]


# ---------- H2：lease refspec 目标侧（冒号后必须 batch/） ----------


def test_h2_lease_refspec_dst_side_blocked():
    assert _blocked(_bash("git push --force-with-lease origin batch/b1:dev"))
    assert _blocked(_bash("git push --force-with-lease origin batch/b1:refs/heads/dev"))


def test_h2_lease_refspec_batch_dst_allowed():
    assert _bash("git push --force-with-lease origin batch/b1:refs/heads/batch/b1") is None
    assert _bash("git push --force-with-lease origin dev:batch/b8-hardening") is None
    # 无冒号裸 refspec 沿用既有子串规则
    assert _bash("git push --force-with-lease origin batch/b8-hardening") is None


def test_h2_lease_refspec_empty_dst_blocked():
    """dst 为空（删除形态/残缺 refspec）fail-closed 拦。"""
    assert _blocked(_bash("git push --force-with-lease origin batch/b1:"))


def test_lease_redirect_target_not_push_arg():
    """重定向 token 及其目标文件不算 push 参数（batch 目标 lease 放行不受影响）。"""
    assert _bash("git push --force-with-lease origin batch/b1 > push.log") is None
    assert _bash("git push --force-with-lease origin batch/b1 2>&1") is None


# ---------- M4：commit -a / add -u / :/ / 通配暂存 ----------


def test_m4_commit_staging_all_blocked():
    assert _blocked(_bash("git commit -am test"))
    assert _blocked(_bash("git commit -a -m test"))
    assert _blocked(_bash("git commit --all -m test"))
    assert _blocked(_bash("git commit -qam test"))


def test_m4_commit_amend_not_blocked(monkeypatch):
    monkeypatch.setattr(hg, "_staged_stats", lambda cwd=None: None)
    assert _bash('git commit --amend -m "fix: x"') is None


def test_m4_add_update_blocked():
    assert _blocked(_bash("git add -u"))
    assert _blocked(_bash("git add --update"))


def test_m4_add_root_and_wildcard_blocked():
    assert _blocked(_bash("git add :/"))
    assert _blocked(_bash("git add :/docs"))
    assert _blocked(_bash("git add '*.py'"))
    assert _blocked(_bash("git add docs/*.py"))
    assert _blocked(_bash("git add *"))


def test_add_explicit_paths_still_allowed():
    assert _bash("git add scripts/gates/hook_guard.py docs/README.md") is None
    assert _bash("git add 'docs/review notes.md'") is None


# ---------- 硬拦 4：手改同步产物目录 ----------


def test_claude_skills_write_blocked():
    out = _path("Write", ".claude/skills/literature_review/SKILL.md")
    assert _blocked(out)
    assert "sync_claude_skills" in out["reason"]


def test_claude_skills_edit_blocked_windows_path():
    out = _path("Edit", "S:\\repo\\.claude\\skills\\literature_review\\SKILL.md")
    assert _blocked(out)


def test_ide_sync_dirs_blocked():
    for d in (".cursor", ".trae", ".qoder"):
        out = _path("Write", f"{d}/rules/dev-commit.md")
        assert _blocked(out), d
        assert "sync_dev_skills" in out["reason"], d


def test_source_dirs_not_blocked():
    assert _path("Write", "skills/literature_review/SKILL.md") is None  # skills/ 源=允许
    assert _path("Edit", "docs/dev_skills/dev-commit/SKILL.md") is None


def test_case_variants_blocked():
    """F4：Windows 大小写不敏感 FS 下 .CLAUDE/Skills 与 .Cursor 等同落点，拦。"""
    assert _blocked(_path("Write", ".CLAUDE/Skills/foo/SKILL.md"))
    assert _blocked(_path("Edit", "S:\\repo\\.Claude\\Skills\\foo\\SKILL.md"))
    assert _blocked(_path("Write", ".Cursor/rules/dev-commit.md"))
    assert _blocked(_path("Write", ".Qoder/rules/dev-commit.md"))


def test_lookalike_names_not_blocked():
    """F4 反例：段中间含点号或无点号的相似名不误拦。"""
    assert _path("Write", "docs/my.cursor/notes.md") is None  # 点号前非路径分隔
    assert _path("Write", "apps/cursorThing/src/x.ts") is None  # 无点号前缀
    assert _path("Write", "docs/dev_skills/.claude-skills-notes.md") is None  # 连字符非目录段


# ---------- 软提示 1：staged 规模 + Task-Spec 尾注 ----------


def test_soft_hint_large_staged_without_task_spec(monkeypatch):
    monkeypatch.setattr(hg, "_staged_stats", lambda cwd=None: (10, 500))
    out = _bash('git commit -m "feat(api): big change"')
    assert out is not None
    assert out["decision"] == "approve"  # 软提示：decision 仍为 approve（不拦）
    assert "Task-Spec" in out["reason"]


def test_soft_hint_suppressed_by_trailer(monkeypatch):
    monkeypatch.setattr(hg, "_staged_stats", lambda cwd=None: (10, 500))
    out = _bash('git commit -m "feat(api): big change\n\nTask-Spec: docs/task_specs/x.md"')
    assert out is None


def test_soft_hint_not_triggered_small_staged(monkeypatch):
    monkeypatch.setattr(hg, "_staged_stats", lambda cwd=None: (2, 40))
    assert _bash('git commit -m "feat(api): small change"') is None


def test_soft_hint_skipped_when_git_unavailable(monkeypatch):
    monkeypatch.setattr(hg, "_staged_stats", lambda cwd=None: None)
    assert _bash('git commit -m "feat(api): any change"') is None


# ---------- 软提示 2：PostToolUse skills 变更 → additionalContext ----------


def test_post_tool_use_skills_hint():
    out = _path("Write", "skills/literature_review/SKILL.md", event="PostToolUse")
    assert out is not None
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "register_skills.py check" in out["hookSpecificOutput"]["additionalContext"]


def test_post_tool_use_ignores_generated_and_docs():
    assert _path("Write", ".claude/skills/x/SKILL.md", event="PostToolUse") is None
    assert _path("Edit", "docs/dev_skills/dev-commit/SKILL.md", event="PostToolUse") is None


def test_post_tool_use_bash_no_opinion():
    assert _bash("git status", event="PostToolUse") is None


# ---------- 协议形态与 CLI ----------


def test_missing_hook_event_defaults_to_pretooluse():
    """DoD D3/D4 载荷形态：省略 hook_event_name 默认 PreToolUse。"""
    payload = {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m x"}}
    assert _blocked(hg.handle(payload))


def test_cli_stdin_cycle(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": staticmethod(
            lambda: '{"tool_name":"Bash","tool_input":{"command":"git push -f origin dev"}}'
        )})(),
    )
    assert hg.main([]) == 0
    out = capsys.readouterr().out
    assert '"decision":"block"' in out  # 紧凑分隔符保证可 grep（D3 形态）


def test_cli_test_input_flag(capsys):
    payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    assert hg.main(["--test-input", json.dumps(payload)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_cli_garbage_input_exits_zero():
    assert hg.main(["--test-input", "not-json"]) == 0


def test_unknown_tool_no_opinion():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Grep", "tool_input": {}}
    assert hg.handle(payload) is None


# ---------- 规则表常量断言 ----------


def test_rule_tables_are_module_constants():
    rule_names = {name for name, _, _ in hg.BASH_HARD_BLOCK_RULES}
    assert rule_names == {"no-verify", "add-all", "add-wildcard", "commit-all", "force-push"}
    assert len(hg.PROTECTED_SYNC_DIRS) == 4
    assert hg.SOFT_HINT_THRESHOLDS == {"max_files": 5, "max_lines": 300}
    assert hg.SOFT_HINT_TRAILER == "Task-Spec:"


def test_git_sub_tokens_handles_global_flags():
    toks = hg._tokens("git -C /repo add -A")
    assert hg._git_sub_tokens(toks, "add") == [["-A"]]
    assert hg._git_sub_tokens(toks, "commit") == []


def test_commit_message_extraction():
    toks = hg._tokens('git commit -m "feat: x" --message="second"')
    assert "feat: x" in hg._commit_message(toks)
    assert "second" in hg._commit_message(toks)

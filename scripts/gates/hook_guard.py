#!/usr/bin/env python3
"""hook_guard —— Claude hooks 单一派发器（AI4SE Harness 重构战役 B3，设计档 §7.6）。

stdin（Claude hooks 协议）：
  {"session_id", "hook_event_name": "PreToolUse|PostToolUse", "tool_name", "tool_input", ...}
  · tool_input：Bash → {"command"}；Write/Edit → {"file_path"}
  · 兼容省略 hook_event_name 的输入（默认按 PreToolUse 处理——历史 DoD 载荷形态）

响应形态（两种协议形态都兼容，本脚本选择标准 decision 字段，账本已记录）：
  硬拦    {"decision":"block","reason":"…"}                                  （PreToolUse）
  软提示  {"decision":"approve","reason":"[soft-hint] …"}（不拦，B3 规格冻结形态） （PreToolUse）
  上下文  {"hookSpecificOutput":{…,"additionalContext":…}}（PostToolUse）
  无意见  空输出、退出码 0

规则表（模块级常量，pytest 可断言）：
  五硬拦（Bash×4 + Write|Edit×1）：
    no-verify     git commit 带 --no-verify / -n
    add-all       git add -A / --all / . / ./ / -u / --update / :/（整树暂存，M4）
    add-wildcard  git add 路径 token 含 * 通配（如 '*.py'，通配暂存，M4）
    commit-all    git commit -a / --all / 短簇含 a（提交时自动整树暂存，M4）
    force-push    git push --force / -f / 短簇含 f / +前缀 refspec 一律拦；--force-with-lease 仅放行
                  目标全为 refs 含 batch/ 的战役批次分支（D-9 裁决：rebase 后受控同步）；
                  refspec src:dst 形态看 dst 侧——冒号后必须含 batch/（H2）
    protected-dir Write/Edit 落在 .claude/skills/ .cursor/ .trae/ .qoder/
                  （大小写不敏感；同步产物，禁手改）
  命令先按 shell 分段（引号外出现 ; && || | & 换行即切段，引号感知；H1）再逐段检测；
  `2>&1` 类重定向被 & 切开后随段自然归属，不算 push 参数。
  两软提示（不拦）：
    task-spec-hint commit 且 staged 超 5 文件或 300 行且 message 无 "Task-Spec:" 尾注
    skills-sync    PostToolUse 后 skills/ 源变更 → 提示跑 register_skills.py check（不代跑）

人工验证：python scripts/gates/hook_guard.py --test-input
  '{"tool_name":"Bash","tool_input":{"command":"git push --force origin dev"}}'
纯标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys

# ---------------------------------------------------------------------------
# 规则表（模块级常量，供 pytest 直接断言）
# ---------------------------------------------------------------------------

GIT_GLOBAL_FLAGS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

# 重定向 token（> >> 2> 2>&1 …）：push 参数收集在此终止（重定向目标是文件不是 refspec）
_REDIRECT_TOKEN_RE = re.compile(r"^\d*>{1,2}")

# (规则名, 检测器(tokens)->bool, 拦截理由) —— 检测器定义见下
BASH_HARD_BLOCK_RULES = (
    (
        "no-verify",
        "_is_commit_no_verify",
        "git commit 带 --no-verify/-n 被硬拦：跳过门禁的提交不允许。"
        "请去掉该参数，让 pre-commit / CI 门禁照常生效。",
    ),
    (
        "add-all",
        "_is_add_all",
        "git add -A / -u / --all / . / :/ 被硬拦：禁止整树暂存（战役纪律）。"
        "请逐个显式 git add <path>。",
    ),
    (
        "add-wildcard",
        "_is_add_wildcard",
        "git add 通配路径（如 git add '*.py'）被硬拦：通配暂存等效扩大暂存面，"
        "违反显式路径纪律。请逐个显式 git add <path>。",
    ),
    (
        "commit-all",
        "_is_commit_staging_all",
        "git commit -a / --all 被硬拦：-a 自动暂存全部已跟踪修改，等效整树暂存（战役纪律）。"
        "请先逐个显式 git add <path>，再 git commit。",
    ),
    (
        "force-push",
        "_is_force_push",
        "force push（--force / -f / 短簇含 f / +refspec，及非批次目标上的 --force-with-lease）"
        "被硬拦。--force-with-lease 仅当推送目标全为 batch/* 批次分支时放行（D-9 裁决）。"
        "共享分支历史改写请走新分支 + PR。",
    ),
)

# (目录标记 regex, 拦截理由) —— Write/Edit 的 file_path 命中即硬拦
# 全部 re.I：Windows 大小写不敏感文件系统下 .CLAUDE/Skills 与 .claude/skills 同落点（F4）
PROTECTED_SYNC_DIRS = (
    (
        re.compile(r"(^|/)\.claude/skills(/|$)", re.I),
        ".claude/skills/ 是自动生成目录（源自 skills/），禁止手改。"
        "请改 skills/ 下对应源文件，然后运行 python scripts/sync_claude_skills.py。",
    ),
    (
        re.compile(r"(^|/)\.cursor(/|$)", re.I),
        ".cursor/ 是 docs/dev_skills/ 的同步产物，禁止手改。"
        "请改 docs/dev_skills/ 下源文件，然后运行 python docs/dev_skills/sync_dev_skills.py。",
    ),
    (
        re.compile(r"(^|/)\.trae(/|$)", re.I),
        ".trae/ 是 docs/dev_skills/ 的同步产物，禁止手改。"
        "请改 docs/dev_skills/ 下源文件，然后运行 python docs/dev_skills/sync_dev_skills.py。",
    ),
    (
        re.compile(r"(^|/)\.qoder(/|$)", re.I),
        ".qoder/ 是 docs/dev_skills/ 的同步产物，禁止手改。"
        "请改 docs/dev_skills/ 下源文件，然后运行 python docs/dev_skills/sync_dev_skills.py。",
    ),
)

# 软提示阈值（B3 规格 §2.3-5：staged >5 文件或 >300 行）
SOFT_HINT_THRESHOLDS = {"max_files": 5, "max_lines": 300}
SOFT_HINT_TRAILER = "Task-Spec:"

SKILLS_SOURCE_RE = re.compile(r"(^|/)skills(/|$)", re.I)
SKILLS_GENERATED_RE = re.compile(r"(^|/)\.claude/skills(/|$)", re.I)

# ---------------------------------------------------------------------------
# 命令解析助手
# ---------------------------------------------------------------------------


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def split_shell_segments(command: str) -> list[str]:
    """按引号外 shell 分隔符切段（H1）。

    引号（单/双）内不切；引号外反斜杠转义下一字符；``;`` ``&&`` ``||`` ``|``
    ``&`` 与换行均为切段点。切出的每个段独立走检测器，杜绝
    ``git add -A; git status`` 这类分隔符粘连 token（shlex 切不出分隔符）的
    逃逸。``2>&1`` 类重定向会被 ``&`` 切开——碎片随段自然归属，不参与
    git 参数判定（_is_force_push 的重定向终止逻辑兜底）。纯标准库手写。
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if quote is not None:
            current.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < n:
                current.append(command[i + 1])
                i += 1
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            current.append(ch)
            current.append(command[i + 1])
            i += 2
            continue
        cut: str | None = None
        if command.startswith("&&", i):
            cut = "&&"
        elif command.startswith("||", i):
            cut = "||"
        elif ch in (";", "|", "&", "\n"):
            cut = ch
        if cut is not None:
            segments.append("".join(current))
            current = []
            i += len(cut)
            continue
        current.append(ch)
        i += 1
    segments.append("".join(current))
    return [seg.strip() for seg in segments if seg.strip()]


def _git_sub_tokens(tokens: list[str], sub: str) -> list[list[str]]:
    """返回所有 `git <sub> …` 中 <sub> 之后的 token 列表（处理 git -C xx 等全局旗标）。"""
    results: list[list[str]] = []
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i] == "git" or tokens[i].endswith("/git"):
            j = i + 1
            while j < n and tokens[j].startswith("-"):
                if tokens[j] in GIT_GLOBAL_FLAGS_WITH_VALUE and j + 1 < n:
                    j += 2
                else:
                    j += 1
            if j < n and tokens[j] == sub:
                results.append(tokens[j + 1 :])
                i = j + 1
            else:
                i = j  # j >= i+1，必然前进
        else:
            i += 1
    return results


def _is_commit_no_verify(tokens: list[str]) -> bool:
    for rest in _git_sub_tokens(tokens, "commit"):
        for tok in rest:
            if tok == "--no-verify" or tok.startswith("--no-verify="):
                return True
            # git commit 的短旗标簇里出现 n 即 --no-verify（-n / -nm / -an…）
            if re.fullmatch(r"-[A-Za-z]+", tok) and "n" in tok[1:]:
                return True
    return False


def _is_add_all(tokens: list[str]) -> bool:
    banned = {"-A", "--all", ".", "./", "-u", "--update"}
    for rest in _git_sub_tokens(tokens, "add"):
        for tok in rest:
            if tok in banned or tok.startswith("--all="):
                return True
            # M4：:/ 与 :/path = 从仓库根整树暂存
            if tok == ":/" or tok.startswith(":/"):
                return True
    return False


def _is_add_wildcard(tokens: list[str]) -> bool:
    for rest in _git_sub_tokens(tokens, "add"):
        for tok in rest:
            # M4：路径 token 含 * 通配（'*.py' / docs/*.py）——通配暂存
            if not tok.startswith("-") and "*" in tok:
                return True
    return False


def _is_commit_staging_all(tokens: list[str]) -> bool:
    for rest in _git_sub_tokens(tokens, "commit"):
        for tok in rest:
            if tok == "-a" or tok == "--all":
                return True
            # M4：git commit 短旗标簇里出现 a 即 -a（-a / -am…），
            # 对齐 no-verify 的 n 簇判法（--amend 长旗标不受影响）
            if re.fullmatch(r"-[A-Za-z]+", tok) and "a" in tok[1:]:
                return True
    return False


def _is_force_push(tokens: list[str]) -> bool:
    # D-9 裁决：--force-with-lease 在推送目标全为 batch/* 战役批次分支（orchestrator
    # 对 rebase 后分支的受控同步，lease 形态在远端意外前移时必然失败）时放行；
    # 其余一切 force 形态、以及非批次目标上的 lease，一律拦。
    _REMOTE_NAMES = {"origin", "upstream"}
    for rest in _git_sub_tokens(tokens, "push"):
        has_lease = False
        has_other_force = False
        for tok in rest:
            if tok == "--force-with-lease" or tok.startswith("--force-with-lease="):
                has_lease = True
                continue
            if tok == "-f" or tok == "--force" or tok.startswith("--force="):
                has_other_force = True
            # F3：短旗标簇含 f（-fq / -f …）——push 语境短旗标里 f 只能是 --force，
            # 与 no-verify 的短簇判定对齐
            if re.fullmatch(r"-[A-Za-z]+", tok) and "f" in tok[1:]:
                has_other_force = True
            # F3：+ 前缀 refspec 强推（+dev 隐式 +dev:dev / +dev:dev 显式），不限冒号形态
            if tok.startswith("+") and len(tok) > 1:
                has_other_force = True
        if has_other_force:
            return True
        if not has_lease:
            continue
        # 仅 lease 形态：非旗标 token 里，除远端名外必须全部指向 batch/*。
        # 管道/重定向之后的 token 属于其他 shell 阶段，不作 push 参数参与判定
        # （重定向 token 及其目标文件同样终止收集，如 `> log 2>&1`）。
        _SHELL_BREAK = {"|", "||", "&&", ";", "&", ">", ">>", "2>", "2>>", "2>&1"}
        args: list[str] = []
        for tok in rest:
            if tok in _SHELL_BREAK or _REDIRECT_TOKEN_RE.match(tok):
                break
            args.append(tok)
        positionals = [tok for tok in args if not tok.startswith("-")]
        batchish: list[str] = []
        others: list[str] = []
        for tok in positionals:
            if tok in _REMOTE_NAMES:
                continue
            # H2：refspec src:dst 看目标侧（冒号后）——dst 必须含 batch/，
            # `batch/b1:dev` 的 src 含 batch/ 不算数；dst 为空（删除/残缺形态）
            # fail-closed 拦。无冒号裸 refspec 沿用整 token 子串规则。
            target = tok.rsplit(":", 1)[1] if ":" in tok else tok
            (batchish if "batch/" in target else others).append(tok)
        if not batchish or others:
            return True
    return False


def _commit_message(tokens: list[str]) -> str:
    parts: list[str] = []
    for rest in _git_sub_tokens(tokens, "commit"):
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok in ("-m", "--message") and i + 1 < len(rest):
                parts.append(rest[i + 1])
                i += 2
                continue
            if tok.startswith("--message="):
                parts.append(tok.split("=", 1)[1])
                i += 1
                continue
            if re.fullmatch(r"-[A-Za-z]*m[A-Za-z]*", tok) and i + 1 < len(rest):
                parts.append(rest[i + 1])
                i += 2
                continue
            i += 1
    return "\n".join(parts)


def _staged_stats(cwd: str | None = None):
    """(staged 文件数, 变更行数)；失败返回 None。只读命令，PreToolUse 阶段允许起进程。"""
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--numstat"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
    except Exception:  # noqa: BLE001 - hook 必须 fail-open：staged 统计失败一律按"无数据"处理
        return None
    if r.returncode != 0:
        return None
    files = lines = 0
    for row in r.stdout.splitlines():
        cols = row.split("\t")
        if len(cols) < 3:
            continue
        files += 1
        for v in cols[:2]:
            try:
                lines += int(v)
            except ValueError:
                pass  # 二进制文件显示 "-"
    return files, lines


# ---------------------------------------------------------------------------
# 规则表检测器绑定（getattr 保持在常量定义后执行一次）
# ---------------------------------------------------------------------------

for _rule in BASH_HARD_BLOCK_RULES:
    if not callable(globals().get(_rule[1])):  # 启动期自检：检测器名必须可解析（等价原 assert）
        raise RuntimeError(f"检测器缺失: {_rule[1]}")

BASH_HARD_BLOCK_RULES = tuple(
    (name, globals()[det], reason) for name, det, reason in BASH_HARD_BLOCK_RULES
)

# ---------------------------------------------------------------------------
# 派发
# ---------------------------------------------------------------------------


def _pre_bash(command: str, cwd: str | None = None):
    # H1：先按 shell 分隔符切段再逐段检测——段内 token 才是真实 git 参数面
    token_lists = [
        toks for toks in (_tokens(seg) for seg in split_shell_segments(command)) if toks
    ]
    for tokens in token_lists:
        for name, detector, reason in BASH_HARD_BLOCK_RULES:
            if detector(tokens):
                return {"decision": "block", "reason": f"[hook_guard:{name}] {reason}"}
    # 软提示：commit 规模超阈值且 message 无 Task-Spec: 尾注（软=不拦，decision 仍 approve）
    for tokens in token_lists:
        if not _git_sub_tokens(tokens, "commit"):
            continue
        if SOFT_HINT_TRAILER not in _commit_message(tokens):
            stats = _staged_stats(cwd)
            if stats:
                files, lines = stats
                over_files = files > SOFT_HINT_THRESHOLDS["max_files"]
                over_lines = lines > SOFT_HINT_THRESHOLDS["max_lines"]
                if over_files or over_lines:
                    return {
                        "decision": "approve",
                        "reason": (
                            f"[hook_guard:task-spec-hint] 软提示（不拦）：本次提交暂存 "
                            f"{files} 个文件 / {lines} 行变更，超过阈值（>5 文件或 >300 行），且 "
                            f"commit message 无 '{SOFT_HINT_TRAILER}' 尾注。复杂任务请先确认 "
                            f"docs/task_specs/ 已有任务规格，或在 message 尾部加 "
                            f"`{SOFT_HINT_TRAILER} <spec 路径>` 消除本提示。"
                        ),
                    }
    return None


def _normalized_path(raw_path: str) -> str:
    return (raw_path or "").replace("\\", "/")


def _pre_path(raw_path: str):
    path = _normalized_path(raw_path)
    if not path:
        return None
    for pattern, reason in PROTECTED_SYNC_DIRS:
        if pattern.search(path):
            return {"decision": "block", "reason": f"[hook_guard:protected-dir] {reason}"}
    return None


def _post_path(raw_path: str):
    path = _normalized_path(raw_path)
    if not path:
        return None
    if SKILLS_GENERATED_RE.search(path) or not SKILLS_SOURCE_RE.search(path):
        return None
    # B3 规格 §2.3-5：PostToolUse 不宜再起进程——只输出提示，不代跑 register_skills.py
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "[hook_guard:skills-sync] 检测到 skills/ 源目录变更：请运行 "
                "python scripts/register_skills.py check 校验注册一致性，并按需运行 "
                "python scripts/sync_claude_skills.py 同步 .claude/skills/（本提示不代跑命令）。"
            ),
        }
    }


def handle(payload: dict):
    event = payload.get("hook_event_name") or "PreToolUse"
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    cwd = payload.get("cwd")

    if event == "PreToolUse":
        if tool == "Bash":
            return _pre_bash(tool_input.get("command") or "", cwd)
        if tool in ("Write", "Edit", "NotebookEdit"):
            path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            return _pre_path(path)
        return None

    if event == "PostToolUse":
        if tool in ("Write", "Edit", "NotebookEdit"):
            path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            return _post_path(path)
        return None

    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Claude hooks 单一派发器：四硬拦（--no-verify/add -A|./force push/"
        "手改同步产物）+ 两软提示"
    )
    parser.add_argument(
        "--test-input",
        default=None,
        help='跳过 stdin，直接解析该 JSON 载荷（人工验证用，如 \'{"tool_name":"Bash",...}\'）',
    )
    args = parser.parse_args(argv)

    raw = args.test_input if args.test_input is not None else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print("hook_guard: 输入不是合法 JSON，无意见放行", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        return 0

    out = handle(payload)
    if out is not None:
        # 紧凑分隔符保证 "decision":"block" 原样可 grep（DoD D3 形态）；
        # ensure_ascii=True 规避 Windows 控制台编码差异（\u 转义对 JSON 解析方等价）
        print(json.dumps(out, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

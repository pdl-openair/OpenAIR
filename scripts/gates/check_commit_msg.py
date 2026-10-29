#!/usr/bin/env python3
"""commit message 校验器（AI4SE Harness 重构战役 B3）。

规则权威 = spec/collaboration/git_commit_rules.md（B2 收编迁移后的规范位置，
rebase 至 024c5d1f 时撤销旧路径括注），以下常量是该文件实际条文的机器投影：

  格式   ：``<type>(<scope>): <summary>``（scope 必填）
  type   ：feat / fix / docs / refactor / style / test / chore / revert
  禁用   ：update / modify / change / optimize / misc / wip（以规则文件条文为准，含 change）
  summary：非空、非泛化词（update/modify/fix bug…）
  署名   ：AI 参与的提交须含 ``Co-Authored-By:`` 行（本仓现约定 + Claude Code 尾注）；
           脚本不校验邮箱域名（多工具并存），只校验存在时的格式 ``Name <email>``。
           纯人工提交可无该行；如需强制（CI/战役用）传 --require-coauthor。

用法：
  python scripts/gates/check_commit_msg.py --file <path>     # 或 --file - 读 stdin
  cat msg.txt | python scripts/gates/check_commit_msg.py     # 无 --file 时读 stdin

退出码：0=全部 PASS；1=存在 FAIL。输出逐项 PASS/FAIL 清单。纯标准库。
"""

from __future__ import annotations

import argparse
import re
import sys

# ---------------------------------------------------------------------------
# 常量（模块级，供 pytest 直接断言；条文来源见模块 docstring）
# ---------------------------------------------------------------------------

ALLOWED_TYPES = ("feat", "fix", "docs", "refactor", "style", "test", "chore", "revert")
FORBIDDEN_TYPES = ("update", "modify", "change", "optimize", "misc", "wip")
RULES_REF = "spec/collaboration/git_commit_rules.md"

SUBJECT_RE = re.compile(r"^(?P<type>[A-Za-z]+)\((?P<scope>[^():]+)\): (?P<summary>\S.*)$")
GENERIC_SUMMARIES = {
    "update",
    "modify",
    "change",
    "optimize",
    "misc",
    "wip",
    "fix bug",
    "fix bugs",
    "add things",
    "update files",
    "modify files",
    "some changes",
}
COAUTHOR_LINE_RE = re.compile(r"^\s*co-authored-by:[ \t]*(?P<value>.+)$", re.I)
COAUTHOR_VALUE_RE = re.compile(r"^(?P<name>[^<>]+?)\s*<(?P<email>[^@\s<>]+@[^@\s<>]+)>\s*$")

# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate(message: str, require_coauthor: bool = False) -> list[tuple[bool, str]]:
    """返回 [(ok, 说明), ...]；任一 False 即不合规。"""
    results: list[tuple[bool, str]] = []
    lines = message.splitlines()
    subject = lines[0].rstrip() if lines else ""
    body_lines = lines[1:] if len(lines) > 1 else []

    if not subject.strip():
        results.append((False, "提交说明为空"))
        return results

    m = SUBJECT_RE.match(subject)
    if not m:
        results.append(
            (
                False,
                f"标题不符合 `<type>(<scope>): <summary>`（scope 必填）：{subject!r}；"
                f"type 只能取 {'/'.join(ALLOWED_TYPES)}（规则={RULES_REF}）",
            )
        )
        # 尝试给出更具体的禁用 type 提示
        loose = re.match(r"^\s*([A-Za-z]+)[(:]", subject)
        if loose and loose.group(1).lower() in FORBIDDEN_TYPES:
            results.append(
                (False, f"type `{loose.group(1)}` 在禁用清单（{'/'.join(FORBIDDEN_TYPES)}）")
            )
        return results

    ctype = m.group("type").lower()
    scope = m.group("scope").strip()
    summary = m.group("summary").strip()
    results.append((True, f"标题格式: {ctype}({scope}): {summary[:50]}"))

    if ctype in ALLOWED_TYPES:
        results.append((True, f"type 合法: {ctype}"))
    elif ctype in FORBIDDEN_TYPES:
        results.append(
            (False, f"type `{ctype}` 禁用（{'/'.join(FORBIDDEN_TYPES)}）；规则={RULES_REF}")
        )
    else:
        results.append(
            (False, f"type `{ctype}` 不在合法清单（{'/'.join(ALLOWED_TYPES)}）；规则={RULES_REF}")
        )

    if scope:
        results.append((True, f"scope 非空: {scope}"))
    else:
        results.append((False, "scope 为空"))

    if summary.lower().rstrip(".。") in GENERIC_SUMMARIES or len(summary) < 3:
        results.append((False, f"summary 过于泛化或过短: {summary!r}（要写具体改了什么）"))
    else:
        results.append((True, f"summary 具体: {summary[:50]}"))

    coauthor_values = []
    for line in body_lines:
        cm = COAUTHOR_LINE_RE.match(line)
        if cm:
            coauthor_values.append(cm.group("value").strip())
    if coauthor_values:
        for value in coauthor_values:
            if COAUTHOR_VALUE_RE.match(value):
                results.append((True, f"Co-Authored-By 格式合法: {value}"))
            else:
                results.append(
                    (False, f"Co-Authored-By 格式应为 `Name <email>`（不校验域名）: {value!r}")
                )
    elif require_coauthor:
        results.append(
            (
                False,
                "缺少 Co-Authored-By 行（--require-coauthor：AI 参与的提交须在 body 末尾追加，"
                "如 `Co-Authored-By: Claude Code <noreply@anthropic.com>`）",
            )
        )
    else:
        results.append((True, "无 Co-Authored-By 行（纯人工提交允许；格式存在时才校验）"))

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:  # noqa: S110 - 编码重配置仅最佳努力，终端不支持时静默降级
            pass
    parser = argparse.ArgumentParser(description=f"commit message 校验器（规则={RULES_REF}）")
    parser.add_argument("--file", default=None, help="commit message 文件路径；`-` 或缺省=读 stdin")
    parser.add_argument(
        "--require-coauthor",
        action="store_true",
        help="强制要求 Co-Authored-By 行（AI 参与提交的战役/CI 场景）",
    )
    args = parser.parse_args(argv)

    if args.file and args.file != "-":
        try:
            with open(args.file, encoding="utf-8") as fh:
                message = fh.read()
        except OSError as exc:
            print(f"FAIL 输入读取失败: {exc}")
            return 1
    else:
        message = sys.stdin.read()

    results = validate(message, require_coauthor=args.require_coauthor)
    passed = failed = 0
    for ok, detail in results:
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'} {detail}")
    print("----")
    print(f"check_commit_msg: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

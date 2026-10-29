#!/usr/bin/env python3
"""变更风险分级器（AI4SE Harness 重构战役 B3，设计档 §7.7）。

判定表（与 spec/workflows/review-workflow.md 对齐——规则正文为 B2 域，本注释是冻结投影；
high→必须人审（CODEOWNERS），评审动作映射见该 spec）：

=====================================================================
| 级别   | 判定（单路径匹配顺序：high 模式 → low 模式 → medium 模式；变更集整体取最高） |
|--------|------------------------------------------------------------------|
| high   | · 路径含 auth / token（子串，保守匹配）                          |
|        | · 路径含 schemas 目录段（^|/)schemas?/）                         |
|        | · skills/registry* 或 skills/skill-bindings*（注册表与绑定）     |
|        | · spec/enforcement/ 下（强制矩阵自身变更）                       |
|        | · 存在文件删除（--from-diff：git diff --diff-filter=D 非空；     |
|        |   仅删行不删文件的"净删除>200 行删除性重构"不触发本条——           |
|        |   简化实现按"是否有文件被删除"判定，--files 模式无法探测删除）    |
| medium | · services/ apps/ packages/ 逻辑变更                             |
|        | · 未命中任何模式的路径（保守归 medium，防漏判）                   |
| low    | · docs/ spec/ scripts/ fixtures/ verification/ .github/          |
|        | · 测试路径（(^|/)tests?/、test_*.py、*_test.py、*.test.* 等）    |
|        | · 根目录单段文件（配置类：ruff.toml、.pre-commit-config.yaml…）  |
=====================================================================

用法：
  python scripts/gates/classify_change_risk.py --files <path> [<path> ...]
  python scripts/gates/classify_change_risk.py --from-diff <base>   # git diff base...HEAD

输出：首行 `risk: high|medium|low`，其后逐条命中理由。退出码恒 0（本脚本是分级器不是门禁，
阻断策略由消费方（review-workflow / B5）决定）。纯标准库。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# 判定模式（模块级常量，供 pytest 直接断言；语义见模块 docstring 判定表）
# ---------------------------------------------------------------------------

HIGH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("auth 子串", re.compile(r"auth", re.I)),
    ("token 子串", re.compile(r"token", re.I)),
    ("schemas 目录段", re.compile(r"(^|/)schemas?/")),
    ("skills 注册表", re.compile(r"^skills/registry")),
    ("skills 绑定表", re.compile(r"^skills/skill-bindings")),
    ("spec 强制配置", re.compile(r"^spec/enforcement/")),
)
MEDIUM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("services/", re.compile(r"^services/")),
    ("apps/", re.compile(r"^apps/")),
    ("packages/", re.compile(r"^packages/")),
)
LOW_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("docs/", re.compile(r"^docs/")),
    ("spec/", re.compile(r"^spec/")),
    ("scripts/", re.compile(r"^scripts/")),
    ("fixtures/", re.compile(r"^fixtures/")),
    ("verification/", re.compile(r"^verification/")),
    (".github/", re.compile(r"^\.github/")),
    ("测试路径", re.compile(r"(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.py$|\.test\.|\.spec\.")),
    ("根目录配置文件", re.compile(r"^[^/]+$")),
)

RANK = {"low": 0, "medium": 1, "high": 2}


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------


def classify_path(path: str) -> tuple[str, str]:
    """单个路径 → (级别, 理由)。

    匹配顺序：high（安全敏感/删除类模式）→ low（文档/测试/脚本等低危模式，
    优先于产品目录命中——services 下的 tests 仍是 low）→ medium（产品逻辑目录）
    → 未命中保守归 medium。
    """
    for level, table in (
        ("high", HIGH_PATTERNS),
        ("low", LOW_PATTERNS),
        ("medium", MEDIUM_PATTERNS),
    ):
        for name, pattern in table:
            if pattern.search(path):
                return level, f"命中 {name}"
    return "medium", "未命中判定表任何模式，保守归 medium"


def classify(paths: list[str], deleted: list[str] | None = None) -> tuple[str, list[str]]:
    """整个变更集 → (最终级别, 理由列表)。删除文件直接 high。"""
    deleted = deleted or []
    reasons: list[tuple[str, str]] = []
    for path in paths:
        level, reason = classify_path(path)
        reasons.append((level, f"{path} {reason}"))
    for path in deleted:
        reasons.append(("high", f"{path} 被删除（git diff --diff-filter=D）"))
    final = "low"
    for level, _ in reasons:
        if RANK[level] > RANK[final]:
            final = level
    return final, [f"{level}| {reason}" for level, reason in reasons]


# ---------------------------------------------------------------------------
# --from-diff 支持
# ---------------------------------------------------------------------------


def _git_diff_name_status(base: str) -> str:
    """git diff --name-status base...HEAD（失败回退两段式 base）。返回原始 stdout。"""
    for revspec in (f"{base}...HEAD", base):
        try:
            r = subprocess.run(
                ["git", "diff", "--name-status", revspec],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception:  # noqa: BLE001, S112 - revspec 回退链：两段全败由末路 RuntimeError 兜底
            continue
        if r.returncode == 0:
            return r.stdout
    raise RuntimeError(f"git diff {base} 失败（不在 git 仓库或 base 不存在）")


def parse_name_status(out: str) -> tuple[list[str], list[str]]:
    """解析 --name-status 输出 → (变更路径列表, 删除路径列表)。"""
    paths: list[str] = []
    deleted: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        status = cols[0][0].upper()
        if status == "D":
            deleted.append(cols[-1])
        elif status == "R" and len(cols) >= 3:
            paths.append(cols[-1])  # 重命名取新路径
        else:
            paths.append(cols[-1])
    return paths, deleted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:  # noqa: S110 - 编码重配置仅最佳努力，终端不支持时静默降级
            pass
    parser = argparse.ArgumentParser(
        description="变更风险分级器（判定表见脚本头部注释，规则正文=spec/workflows/review-workflow.md）"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--files", nargs="+", metavar="PATH", help="变更文件列表")
    mode.add_argument(
        "--from-diff", metavar="BASE", help="用 git diff base...HEAD 取变更集（含删除探测）"
    )
    args = parser.parse_args(argv)

    if args.from_diff:
        try:
            out = _git_diff_name_status(args.from_diff)
        except RuntimeError as exc:
            print(f"FAIL {exc}")
            return 2
        paths, deleted = parse_name_status(out)
    else:
        paths, deleted = [p.replace("\\", "/") for p in args.files], []

    final, reasons = classify(paths, deleted)
    print(f"risk: {final}")
    for reason in reasons:
        print(reason)
    if args.from_diff is None and not paths:
        print("FAIL 未提供任何变更路径")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

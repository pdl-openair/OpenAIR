#!/usr/bin/env python3
"""STATUS/CHANGELOG 卫生检查（AI4SE Harness 重构战役 B3，设计档 §7.8，SPEC-HYGIENE-01）。

双模式：
  --mode release   release PR 的变更文件列表必含 STATUS.md 或 CHANGELOG.md
                   （B5 的 workflow 对带 release 标签的 PR 调用；文件列表来自 --files
                   或 env PR_FILES）→ 缺失退出 1（阻断：阶段发布必须落状态文档）
  --mode staleness STATUS.md 距最后提交 >14 天且其后有产品提交（services/apps/packages/skills）
                   → stdout 警告文案（退出 0，不阻断——机制化≠全阻断，避免为日志文档卡死产品 PR）

用法：
  python scripts/gates/check_hygiene.py --mode release --files STATUS.md docs/x.md
  python scripts/gates/check_hygiene.py --mode release --from-env
      # env PR_FILES（逗号/换行分隔）
  python scripts/gates/check_hygiene.py --mode staleness

退出码：release 缺失=1；staleness 恒 0。纯标准库。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# 常量（模块级，供 pytest 直接断言）
# ---------------------------------------------------------------------------

RELEASE_REQUIRED = ("STATUS.md", "CHANGELOG.md")
STALENESS_DAYS = 14
PRODUCT_PATHS = ("services", "apps", "packages", "skills")


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# release 模式
# ---------------------------------------------------------------------------


def check_release(files: list[str]) -> tuple[bool, str]:
    hits = [f for f in files if f in RELEASE_REQUIRED]
    if hits:
        return True, f"release PR 包含状态文档变更: {', '.join(sorted(set(hits)))}"
    return (
        False,
        "release PR 缺少 STATUS.md / CHANGELOG.md 变更（SPEC-HYGIENE-01：阶段发布必须落状态文档）。"
        f"本次变更文件（{len(files)} 个）均不含二者。",
    )


# ---------------------------------------------------------------------------
# staleness 模式
# ---------------------------------------------------------------------------


def check_staleness(today=None) -> tuple[bool, str]:
    """返回 (有产品提交且逾期, 文案)。仅警告，不阻断。"""
    today = today or _dt.date.today()
    r = _git(["log", "-1", "--format=%H %cs", "--", "STATUS.md"])
    if r.returncode != 0 or not r.stdout.strip():
        return False, "[staleness] 无 STATUS.md 提交历史（新仓或未跟踪），跳过"
    sha, date_s = r.stdout.strip().split(maxsplit=1)
    try:
        last = _dt.date.fromisoformat(date_s)
    except ValueError:
        return False, f"[staleness] STATUS.md 最后提交日期不可解析: {date_s!r}"
    days = (today - last).days
    if days <= STALENESS_DAYS:
        return False, f"[staleness] STATUS.md {days} 天前更新（阈值 {STALENESS_DAYS} 天），正常"
    count_r = _git(["rev-list", "--count", f"{sha}..HEAD", "--", *PRODUCT_PATHS])
    count = count_r.stdout.strip() if count_r.returncode == 0 else "0"
    if count.isdigit() and int(count) > 0:
        return True, (
            f"[staleness] 警告：STATUS.md 已 {days} 天未更新（>{STALENESS_DAYS} 天），"
            f"且其后有 {count} 个产品提交（{'/'.join(PRODUCT_PATHS)}）。请更新 STATUS.md。"
            "（非阻断提醒，SPEC-HYGIENE-01）"
        )
    return False, f"[staleness] STATUS.md 已 {days} 天未更新，但其间无产品提交，不提醒"


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
        description="STATUS/CHANGELOG 卫生检查（SPEC-HYGIENE-01：release 阻断 + 逾期提醒）"
    )
    parser.add_argument("--mode", required=True, choices=("release", "staleness"))
    parser.add_argument("--files", nargs="+", metavar="PATH", help="release 模式：PR 变更文件列表")
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="release 模式：读 env PR_FILES（逗号/换行分隔，B5 workflow 注入）",
    )
    args = parser.parse_args(argv)

    if args.mode == "release":
        if args.from_env:
            raw = os.environ.get("PR_FILES", "")
            files = [p.strip() for p in raw.replace(",", "\n").splitlines() if p.strip()]
        elif args.files:
            files = [p.replace("\\", "/") for p in args.files]
        else:
            print("FAIL 未提供变更文件列表（--files 或 --from-env）")
            return 2
        ok, detail = check_release(files)
        print(f"{'PASS' if ok else 'FAIL'} {detail}")
        return 0 if ok else 1

    stale, detail = check_staleness()
    print(detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""标签建档/同步工具（AI4SE Harness 重构战役 B7，设计 §10 步骤 2）。

维护门禁消费的标签集（名称+颜色+描述），清单以 B5 实际实现为准
（.github/workflows/required-checks.yml）：

  no-issue-required  pr-evidence issue-link 步骤的 PR 逃生门标签
                     （required-checks.yml "escape label" 分支）
  status/verified    fix 类触产品代码的 PR 所引 issue 必须携带的验证态
                     标签（SPEC-PROC-02）
  release            hygiene 门 release 模式的设计触发标签（SPEC-HYGIENE-01；
                     工具已就位，workflow 接线缺口登记于 ledger/b7.md）

  --check   只校验不写：全部存在且颜色/描述一致 -> 退出 0；有缺失或漂移
            -> 退出 1 并逐条列出（CI 可用）
  （默认）   幂等同步：创建缺失标签、修正颜色/描述漂移；从不删除清单外
            标签，也从不改清单外标签

实现：纯标准库 + gh CLI（label list / label create / label edit），在仓内
任意目录运行（gh 经 git remote 解析仓库）。默认模式含写操作、需要 repo
admin 权限——只能由仓库 admin 手动执行；B7 施工 agent 只以 --check 运行
（战役 agent 不持有也不申请 admin token 的写权限）。

用法：
  python scripts/ci/sync_labels.py --check    # 校验（零写入）
  python scripts/ci/sync_labels.py            # 同步（admin 手动执行）

退出码：0=通过/已同步；1=校验失败（缺失或漂移）；2=gh 调用失败。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

# ---------------------------------------------------------------------------
# 标签清单（名称+颜色+描述）——与 branch-protection-checklist.md 标签节 1:1
# ---------------------------------------------------------------------------

LABEL_MANIFEST: list[dict[str, str]] = [
    {
        "name": "no-issue-required",
        "color": "FBCA04",
        "description": (
            "PR 逃生门：跳过 fix 类 PR 的 issue 关联校验"
            "（SPEC-PROC-02；打标签即审计痕）"
        ),
    },
    {
        "name": "status/verified",
        "color": "0E8A16",
        "description": (
            "issue 验证态：fix 类触产品代码的 PR 所引 issue 必须携带"
            "（SPEC-PROC-02）"
        ),
    },
    {
        "name": "release",
        "color": "1D76DB",
        "description": (
            "release PR 标记：STATUS/CHANGELOG 卫生检查的触发标签"
            "（SPEC-HYGIENE-01）"
        ),
    },
]


def _gh(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``gh`` capturing output; caller inspects returncode/stderr."""
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _normalize_color(color: str) -> str:
    """GitHub/gh color forms ('#0e8a16' / '0E8A16') -> comparable '0E8A16'."""
    return color.strip().lstrip("#").upper()


def list_labels() -> dict[str, dict[str, str]] | None:
    """Return {name: {color, description}} for the current repo, or None on gh failure."""
    proc = _gh("label", "list", "--json", "name,color,description", "--limit", "200")
    if proc.returncode != 0:
        print("[sync_labels] gh label list 失败：", file=sys.stderr)
        print(proc.stderr.strip(), file=sys.stderr)
        return None
    entries = json.loads(proc.stdout or "[]")
    return {
        entry["name"]: {
            "color": _normalize_color(entry.get("color", "")),
            "description": entry.get("description") or "",
        }
        for entry in entries
    }


def diff_manifest(existing: dict[str, dict[str, str]]) -> list[tuple[str, dict[str, str]]]:
    """Return manifest entries that are missing or drifted vs ``existing``."""
    pending: list[tuple[str, dict[str, str]]] = []
    for wanted in LABEL_MANIFEST:
        current = existing.get(wanted["name"])
        if current is None:
            pending.append(("missing", wanted))
        elif (
            _normalize_color(current["color"]) != _normalize_color(wanted["color"])
            or current["description"] != wanted["description"]
        ):
            pending.append(("drift", wanted))
    return pending


def check_mode() -> int:
    """Verify only: exit 0 when everything matches, exit 1 with a pending list."""
    existing = list_labels()
    if existing is None:
        return 2
    pending = diff_manifest(existing)
    if not pending:
        print(f"[sync_labels] PASS: {len(LABEL_MANIFEST)} 个清单标签全部存在且一致")
        return 0
    print(f"[sync_labels] FAIL: {len(pending)} 个标签待建档/待修正（未做任何写入）：")
    for kind, wanted in pending:
        print(f"[sync_labels]   {kind}: {wanted['name']}")
        print(f"[sync_labels]     color: {wanted['color']}")
        print(f"[sync_labels]     description: {wanted['description']}")
    return 1


def sync_mode() -> int:
    """Idempotently create missing labels and fix drift; never delete/touch others."""
    existing = list_labels()
    if existing is None:
        return 2
    pending = diff_manifest(existing)
    if not pending:
        print(f"[sync_labels] 已一致：{len(LABEL_MANIFEST)} 个清单标签无需改动")
        return 0
    failed = 0
    for kind, wanted in pending:
        name = wanted["name"]
        verb_args = (
            ["label", "create", name]
            if kind == "missing"
            else ["label", "edit", name]
        )
        proc = _gh(
            *verb_args,
            "--color",
            wanted["color"],
            "--description",
            wanted["description"],
        )
        if proc.returncode != 0:
            failed += 1
            print(f"[sync_labels] {kind} {name} 失败：{proc.stderr.strip()}")
        else:
            print(f"[sync_labels] {kind} {name} -> {'created' if kind == 'missing' else 'updated'}")
    if failed:
        print(f"[sync_labels] 同步完成，{failed} 个失败")
        return 1
    # 幂等性自证：同步后立即复检
    return check_mode()


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except OSError:  # noqa: S110 - 编码重配置仅最佳努力，终端不支持时静默降级
            pass
    parser = argparse.ArgumentParser(
        description="标签建档/同步（B7 分支保护清单标签；--check 零写入，CI 可用）"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验不写：缺失/漂移退出 1（默认行为是幂等同步，需 admin）",
    )
    args = parser.parse_args(argv)
    return check_mode() if args.check else sync_mode()


if __name__ == "__main__":
    sys.exit(main())

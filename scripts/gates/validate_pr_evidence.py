#!/usr/bin/env python3
"""PR 证据合同校验器（AI4SE Harness 重构战役 B3，设计档 §7.5）。

字段清单与 spec/workflows/pr-workflow.md（= B2 任务规格 §2.8 冻结清单）完全一致，
本脚本与 .github/PULL_REQUEST_TEMPLATE.md 是同一合同的两面投影，不得自创字段：

  必填节：① 改了什么 ② 没改什么 ③ 怎么验证（必含一行 ``验证层级: L<1-6>``）
          ④ 影响面勾选（至少一项 [x]）⑤ CI run 链接 ⑥ 风险分级（high/medium/low）

  验证六层级：L1 已分析 / L2 已自测 / L3 单测通过 / L4 集成验证 / L5 预发验证 / L6 真实 E2E 已验证

CI 链接节规则：含 http 链接，或显式整行 ``N/A（本地验证）``；
fix 类 PR（标题 ``fix(scope):`` / ``fix:`` 开头）不允许 N/A 逃生，必须给 CI 链接
（B3 规格括号语义取严读：fix 是 Reproduce-Before-Fix 价值最高场景）。

用法：
  本地：python scripts/gates/validate_pr_evidence.py --file pr_body.md [--title "fix(api): x"]
  CI  ：python scripts/gates/validate_pr_evidence.py --from-env
        （读 env PR_BODY / PR_TITLE，由 B5 的 workflow 注入）

退出码：0=全部 PASS；1=存在 FAIL（或输入缺失）。输出逐项 PASS/FAIL 清单。纯标准库。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# ---------------------------------------------------------------------------
# 常量（模块级，供 pytest 直接断言）
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = ("改了什么", "没改什么", "怎么验证", "影响面勾选", "CI run 链接", "风险分级")

SECTION_ALIASES = {
    "改了什么": {"改了什么", "本次改了什么"},
    "没改什么": {"没改什么", "没有改什么", "本次没有改什么", "未改动什么"},
    "怎么验证": {"怎么验证", "如何验证", "验证方式"},
    "影响面勾选": {"影响面勾选", "影响面", "是否影响数据 / 接口 / Mock"},
    "CI run 链接": {"CI run 链接", "CI 链接", "CI run", "CI run链接"},
    "风险分级": {"风险分级", "风险或注意事项", "风险与注意事项"},
}

LEVEL_LINE_RE = re.compile(r"验证层级[:：]\s*(L[1-6])(?![0-9])")
LEVEL_VOCAB = {
    "L1": "已分析",
    "L2": "已自测",
    "L3": "单测通过",
    "L4": "集成验证",
    "L5": "预发验证",
    "L6": "真实 E2E 已验证",
}
CHECKED_ITEM_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
PLACEHOLDER_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?$")  # 空行 / 裸 "-" 占位
LOCAL_NA_RE = re.compile(r"^\s*N/A[（(]本地验证[)）]\s*$", re.M)
RISK_LINE_RE = re.compile(r"风险分级[:：]\s*(high|medium|low)\b", re.I)
FIX_TITLE_RE = re.compile(r"^\s*fix(\([^)]*\))?:", re.I)

# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def split_sections(text: str) -> list[tuple[str, str]]:
    """把 markdown 按 ##/### 标题切段，返回 [(标题, 正文), ...]；首个标题之前的内容忽略。"""
    sections: list[tuple[str, str]] = []
    title = None
    lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*#{1,6}\s+(.*?)\s*$", line)
        if m:
            if title is not None:
                sections.append((title, "\n".join(lines)))
            title = m.group(1)
            lines = []
        elif title is not None:
            lines.append(line)
    if title is not None:
        sections.append((title, "\n".join(lines)))
    return sections


def find_section(sections: list[tuple[str, str]], canonical: str):
    """按别名找到对应节，返回正文；不存在返回 None。"""
    for title, body in sections:
        if title in SECTION_ALIASES[canonical]:
            return body
    return None


def has_content(body: str) -> bool:
    """去掉 HTML 注释、空行、裸 '-' 占位后仍有内容 → True。"""
    stripped = COMMENT_RE.sub("", body)
    for line in stripped.splitlines():
        if not PLACEHOLDER_LINE_RE.match(line):
            return True
    return False


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate(body_text: str, title: str = "") -> list[tuple[bool, str]]:
    """返回 [(ok, 说明), ...]；任一 False 即 PR 证据合同不达标。"""
    results: list[tuple[bool, str]] = []
    sections = split_sections(body_text)

    bodies: dict[str, str | None] = {}
    for canonical in REQUIRED_SECTIONS:
        body = find_section(sections, canonical)
        bodies[canonical] = body
        if body is None:
            results.append((False, f"必填节缺失: {canonical}"))
        else:
            results.append((True, f"必填节存在: {canonical}"))

    for canonical in REQUIRED_SECTIONS:
        body = bodies[canonical]
        if body is None:
            continue
        if has_content(body):
            results.append((True, f"必填节非空: {canonical}"))
        else:
            results.append((False, f"必填节为空（仅注释/占位符）: {canonical}"))

    # ③ 验证层级行（先在"怎么验证"节内找，找不到再全文找；两侧均剥注释，同 F1 防注释残留）
    verify_body = COMMENT_RE.sub("", bodies["怎么验证"] or "")
    m = LEVEL_LINE_RE.search(verify_body) or LEVEL_LINE_RE.search(COMMENT_RE.sub("", body_text))
    if m:
        level = m.group(1).upper()
        results.append((True, f"验证层级: {level}（{LEVEL_VOCAB[level]}）"))
    else:
        results.append(
            (
                False,
                "缺少验证层级行：『怎么验证』节必含一行 `验证层级: L<1-6>`"
                "（L1 已分析 / L2 已自测 / L3 单测通过 / L4 集成验证 / "
                "L5 预发验证 / L6 真实 E2E 已验证）",
            )
        )

    # ④ 影响面勾选：至少一个 [x]
    impact_body = bodies["影响面勾选"] or ""
    impact_body_nc = COMMENT_RE.sub("", impact_body)
    if any(CHECKED_ITEM_RE.match(line) for line in impact_body_nc.splitlines()):
        results.append((True, "影响面勾选: 已勾选"))
    else:
        results.append(
            (False, "影响面勾选: 未发现任何勾选项（需要至少一个 `- [x]`，含『均不涉及』）")
        )

    # ⑤ CI run 链接：http 链接，或显式 N/A（本地验证）（fix 类除外——fix 必须给链接）
    # F1 修复：先剥 HTML 注释再匹配——模板注释自带"（http…）/N/A（本地验证）"字样，
    # 未剥注释时空模板正文会借注释残留假通过。
    ci_body = COMMENT_RE.sub("", bodies["CI run 链接"] or "")
    is_fix = bool(title) and bool(FIX_TITLE_RE.match(title))
    if "http" in ci_body:
        results.append((True, "CI run 链接: 已提供"))
    elif LOCAL_NA_RE.search(ci_body):
        if is_fix:
            results.append(
                (False, "CI run 链接: fix 类 PR 不允许 N/A（本地验证），必须提供 CI run 链接")
            )
        else:
            results.append((True, "CI run 链接: 显式 N/A（本地验证）"))
    else:
        results.append((False, "CI run 链接: 缺少 http 链接或显式整行 `N/A（本地验证）`"))

    # ⑥ 风险分级值（F5 加固：先节内后全文，两者都剥注释；大小写不敏感接受、小写报告）
    risk_body = COMMENT_RE.sub("", bodies["风险分级"] or "")
    rm = RISK_LINE_RE.search(risk_body) or RISK_LINE_RE.search(COMMENT_RE.sub("", body_text))
    if rm:
        results.append((True, f"风险分级: {rm.group(1).lower()}"))
    else:
        results.append(
            (False, "风险分级: 缺少 `风险分级: high|medium|low` 行（圈一个，不能留模板原文）")
        )

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
    parser = argparse.ArgumentParser(
        description="PR 证据合同校验器（字段清单=spec/workflows/pr-workflow.md 冻结版）"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--file", help="本地模式：读 markdown 文件（PR 正文）")
    mode.add_argument(
        "--from-env",
        action="store_true",
        help="CI 模式：读 env PR_BODY / PR_TITLE（由 B5 workflow 注入）",
    )
    parser.add_argument("--title", default="", help="PR 标题（本地模式可选；用于 fix 类判定）")
    args = parser.parse_args(argv)

    if args.from_env:
        body = os.environ.get("PR_BODY", "")
        title = os.environ.get("PR_TITLE", "")
        if not body.strip():
            print("FAIL 输入缺失: env PR_BODY 为空（--from-env 模式需要 workflow 注入 PR 正文）")
            return 1
    else:
        try:
            with open(args.file, encoding="utf-8") as fh:
                body = fh.read()
        except OSError as exc:
            print(f"FAIL 输入读取失败: {exc}")
            return 1
        title = args.title

    results = validate(body, title)
    passed = failed = 0
    for ok, detail in results:
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'} {detail}")
    print("----")
    print(f"validate_pr_evidence: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

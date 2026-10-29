#!/usr/bin/env python3
"""Risk-path review gate (M5): auth/token paths demand a reviewed risk level.

``classify_change_risk.py`` classifies auth/token surfaces as HIGH risk,
but it had zero CI consumers - "must be human-reviewed" for such changes
was pure self-report. This gate is the consumer: it reads the PR file list
(``PR_FILES`` env, comma- or newline-separated) plus the PR body
(``PR_BODY`` env), and when any changed path is high-risk the body MUST
carry ``风险分级:`` at medium or above.

High-risk definition (deliberately narrower than classify_change_risk's
conservative substring sweep, per the B8 spec): a path starting with
``services/api/app/auth``, or a ``.py`` path containing token /
credential / jwt / secret (case-insensitive).

Under-reported, missing or unparsable level -> exit 1 with guidance;
satisfied -> prominent "必人审" banner, then pass. PRs touching no
high-risk path pass silently.

Exit codes: 0 = pass, 1 = violation (under-reported risk),
2 = usage/env error (``--from-env`` without PR_FILES set).

Pure Python >= 3.10, standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

AUTH_PREFIX = "services/api/app/auth"
RISK_KEYWORDS = ("token", "credential", "jwt", "secret")

LEVEL_BY_NAME = {"low": 0, "低": 0, "medium": 1, "中": 1, "high": 2, "高": 2}
REQUIRED_LEVEL = 1  # medium

RISK_LEVEL_RE = re.compile(r"风险分级\s*[:：]\s*(\S+)")


def is_high_risk(path: str) -> bool:
    """auth 前缀，或 .py 路径含 token/credential/jwt/secret（不区分大小写）。"""
    normalized = path.strip().replace("\\", "/").lstrip("./")
    if normalized.startswith(AUTH_PREFIX):
        return True
    if not normalized.lower().endswith(".py"):
        return False
    lowered = normalized.lower()
    return any(keyword in lowered for keyword in RISK_KEYWORDS)


def parse_risk_level(body: str) -> int | None:
    """``风险分级:`` 的档位值；缺失/无法解析 → None（fail-closed 由调用方处理）。"""
    match = RISK_LEVEL_RE.search(body or "")
    if match is None:
        return None
    return LEVEL_BY_NAME.get(match.group(1).strip().lower())


def split_files(raw: str) -> list[str]:
    """PR_FILES 支持逗号或换行分隔（workflow 侧 ``tr '\\n' ','`` 形态）。"""
    return [part.strip() for part in re.split(r"[,\n]", raw or "") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Risk-path gate (M5): auth/token paths require 风险分级 >= medium."
    )
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="read PR_FILES / PR_BODY from the environment (CI form)",
    )
    parser.add_argument(
        "--files", default=None, help="explicit file list (comma/newline separated; tests/local)"
    )
    parser.add_argument("--body", default=None, help="explicit PR body text (tests/local)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.from_env:
        if "PR_FILES" not in os.environ:
            print(
                "[risk-paths] PR_FILES not set - the PR file-list pipeline did not run; "
                "refusing to guess (use --files/--body or export PR_FILES)",
                file=sys.stderr,
            )
            return EXIT_ERROR
        files = split_files(os.environ["PR_FILES"])
        body = os.environ.get("PR_BODY", "")
    elif args.files is not None:
        files = split_files(args.files)
        body = args.body or ""
    else:
        print("[risk-paths] nothing to check: pass --from-env or --files", file=sys.stderr)
        return EXIT_ERROR

    hits = [path for path in files if is_high_risk(path)]
    if not hits:
        print(f"[risk-paths] no high-risk path among {len(files)} changed file(s); pass")
        return EXIT_PASS

    level = parse_risk_level(body)
    if level is None:
        print(
            "[risk-paths] BLOCKED: this PR touches high-risk paths but the body has no "
            "parsable '风险分级:' line (accepted: low/低, medium/中, high/高).\n"
            "  在 PR 正文加一行，例如：风险分级: high\n"
            "  并说明认证/凭据面的改动内容与验证方式——这是必人审的准入条件。",
            file=sys.stderr,
        )
        for path in hits:
            print(f"[risk-paths]   high-risk: {path}", file=sys.stderr)
        return EXIT_VIOLATION
    if level < REQUIRED_LEVEL:
        print(
            "[risk-paths] BLOCKED: high-risk paths with 风险分级 below medium - auth/token "
            "surfaces cannot ship self-reported low.\n"
            "  提高分级到 medium/high（或低报不成立时说明理由并请评审人复核）。",
            file=sys.stderr,
        )
        for path in hits:
            print(f"[risk-paths]   high-risk: {path}", file=sys.stderr)
        return EXIT_VIOLATION

    bar = "=" * 62
    print(bar)
    print(f"[risk-paths] 必人审（MANDATORY HUMAN REVIEW）——{len(hits)} 个高风险路径，")
    print("[risk-paths] 风险分级达标；评审人必须实际读过认证/凭据面改动后才可 approve：")
    for path in hits:
        print(f"[risk-paths]   high-risk: {path}")
    print(bar)
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

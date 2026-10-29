#!/usr/bin/env python3
"""Local pre-commit entry point (single dispatcher, graceful degradation).

Each subcommand delegates to the matching gate script or binary. When a tool
is missing from the local machine (e.g. the gitleaks binary, or a gate script
that a later batch has not landed yet), the subcommand prints a warning and
exits 0 so commits are not blocked by personal-environment gaps - CI is the
authoritative layer. ``--strict`` turns degradation into a hard error.

Subcommands: ruff | mypy | placement | gitleaks | pointer | commit-msg.

The ruff subcommand runs a two-pass check (debt 4-F4): the committed-range
delegate (diff_gate, base...HEAD) plus a staged pass - ruff findings on lines
added by staged content are visible at pre-commit time instead of one commit
later. Staged detection is two-point: the merge-base<->index file list and
the HEAD<->index ("--cached" actual state) jointly define the surface; files
staged back to their merge-base content drop out. Git failures degrade to a
warning (warn-and-pass) like every other local gap.

Exit codes mirror the gates: 0 = pass/degraded, 1 = violation,
2 = usage/env error (or degraded-but-strict).

Pure Python >= 3.10, standard library only (reuses diff_gate's pure
diff/violation helpers via import).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

import diff_gate  # sibling gate module: pure parse/filter helpers (④F4 staged pass)

GATES_DIR = os.path.dirname(os.path.abspath(__file__))  # <root>/scripts/gates
ROOT = os.path.dirname(os.path.dirname(GATES_DIR))  # <root>
DEFAULT_BASE = "origin/test"

FUTURE_GATES = {
    "pointer": os.path.join(GATES_DIR, "check_pointer_integrity.py"),  # batch B2
    "commit-msg": os.path.join(GATES_DIR, "check_commit_msg.py"),  # batch B3
}


def run(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=cwd or ROOT,
        check=False,
    )


def probe_module(python: str, module: str) -> bool:
    """True when ``python -m <module>`` is importable in that interpreter."""
    proc = subprocess.run(
        [python, "-m", module, "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def _git_out(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Capture-mode git runner (injectable for unit tests)."""
    return subprocess.run(
        argv,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def staged_scope(root: str, base: str = DEFAULT_BASE, git=_git_out):
    """④F4 两点式 staged 检测 → (staged 文件列表, merge-base sha)；git 失败 → None。

    点1 ``git diff --cached --name-only <merge-base>``（merge-base↔index 并集面）；
    点2 ``git diff --cached --name-only`` 单独跑一次（HEAD↔index，staged 实态对照）。
    共同判定=交集：既 staged 又与 merge-base 有差异的文件才需要 staged 门禁
    （staged 后改回 merge-base 原内容的文件天然出局）。
    """
    mb_proc = git(["git", "merge-base", base, "HEAD"], cwd=root)
    if mb_proc.returncode != 0 or not mb_proc.stdout.strip():
        return None
    merge_base = mb_proc.stdout.strip()
    point1 = git(["git", "diff", "--cached", "--name-only", merge_base], cwd=root)
    point2 = git(["git", "diff", "--cached", "--name-only"], cwd=root)
    if point1.returncode != 0 or point2.returncode != 0:
        return None
    changed_since_base = {
        p.strip().replace("\\", "/") for p in point1.stdout.splitlines() if p.strip()
    }
    staged_now = {p.strip().replace("\\", "/") for p in point2.stdout.splitlines() if p.strip()}
    return sorted(staged_now & changed_since_base), merge_base


def staged_ruff_gate(
    python: str,
    staged: list[str],
    merge_base: str,
    root: str,
    git=_git_out,
    sp=subprocess.run,
) -> int:
    """ruff 检查 staged 新增行（committed-range 三点式 diff 看不见的面）。

    复用 diff_gate 的纯函数解析/过滤；只报 staged 文件上的新增行违规。
    ruff 崩溃/git 失败 → warn 放行（本地降级语义，CI 是权威层）。
    """
    diff_proc = git(
        ["git", "diff", "--no-color", "--unified=0", "--cached", merge_base], cwd=root
    )
    if diff_proc.returncode != 0:
        print(
            f"[run_local] WARNING: staged-ruff unavailable -> degraded: "
            f"git staged diff failed (exit {diff_proc.returncode})",
            file=sys.stderr,
        )
        return 0
    ruff = sp(
        [python, "-m", "ruff", "check", "--output-format", "json", "."],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if ruff.returncode not in (0, 1):
        print(
            f"[run_local] WARNING: staged-ruff unavailable -> degraded: "
            f"ruff crashed (exit {ruff.returncode})",
            file=sys.stderr,
        )
        return 0
    try:
        violations = diff_gate.parse_ruff_json(ruff.stdout or "[]", root)
    except ValueError as exc:
        print(f"[run_local] WARNING: staged-ruff unavailable -> degraded: {exc}", file=sys.stderr)
        return 0
    added = diff_gate.parse_diff(diff_proc.stdout)
    staged_set = set(staged)
    kept = []
    for violation in violations:
        if violation.file not in staged_set:
            continue
        if violation.line is None:
            if violation.file in added.new_files:
                kept.append(violation)
        elif violation.line in added.added.get(violation.file, set()):
            kept.append(violation)
    for item in kept:
        print(f"[run_local] staged-ruff {item.file}:{item.line}: [{item.code}] {item.message}")
    print(
        f"[run_local] staged two-point ruff: {len(kept)} violation(s) on staged lines "
        f"({len(violations)} total, filtered; {len(staged)} staged file(s))"
    )
    return 1 if kept else 0


class GateRunner:
    """Dispatch table with degradation control; injectable for unit tests."""

    def __init__(
        self,
        strict: bool = False,
        runner=run,
        which=shutil.which,
        prober=probe_module,
        python: str | None = None,
        git=_git_out,
    ) -> None:
        self.strict = strict
        self.runner = runner
        self.which = which
        self.prober = prober
        self.python = python or sys.executable
        self.git = git

    def degrade(self, tool: str, reason: str) -> int:
        """Warn-and-pass unless strict (CI / explicit runs must not degrade)."""
        print(f"[run_local] WARNING: {tool} unavailable -> degraded: {reason}", file=sys.stderr)
        if self.strict:
            print(
                f"[run_local] --strict set, refusing to degrade on missing {tool}",
                file=sys.stderr,
            )
            return 2
        return 0

    def delegate(self, argv: list[str], cwd: str | None = None) -> int:
        proc = self.runner(argv, cwd)
        return proc.returncode

    # -- subcommands -------------------------------------------------------

    def cmd_ruff(self, _args: argparse.Namespace) -> int:
        if not self.prober(self.python, "ruff"):
            return self.degrade("ruff", "run `uv sync --project services/api` or pip install ruff")
        # 第一程：committed-range 增量门禁（base...HEAD，原行为不变）
        code = self.delegate(
            [self.python, os.path.join(GATES_DIR, "diff_gate.py"), "--ruff", "--base", DEFAULT_BASE]
        )
        if code != 0:
            return code
        # 第二程（④F4）：staged 两点式补检——staged 内容在三点式 diff 里不可见，
        # 原先最迟下一提交才引爆；现在 pre-commit 当场拦。
        scope = staged_scope(ROOT, DEFAULT_BASE, git=self.git)
        if scope is None:
            return self.degrade(
                "staged-ruff",
                "git staged detection unavailable (two-point); "
                "the committed-range ruff gate above already ran",
            )
        staged, merge_base = scope
        if not staged:
            print("[run_local] staged two-point check: no staged delta beyond HEAD")
            return 0
        return staged_ruff_gate(self.python, staged, merge_base, ROOT, git=self.git)

    def cmd_mypy(self, _args: argparse.Namespace) -> int:
        if not self.prober(self.python, "mypy"):
            return self.degrade("mypy", "run `uv sync --project services/api` or pip install mypy")
        return self.delegate(
            [
                self.python,
                os.path.join(GATES_DIR, "mypy_gate.py"),
                "--base",
                DEFAULT_BASE,
                "--python",
                self.python,
            ]
        )

    def cmd_placement(self, _args: argparse.Namespace) -> int:
        return self.delegate(
            [self.python, os.path.join(GATES_DIR, "placement_guard.py"), "--base", DEFAULT_BASE]
        )

    def cmd_gitleaks(self, _args: argparse.Namespace) -> int:
        binary = self.which("gitleaks")
        if binary is None:
            return self.degrade("gitleaks", "binary not on PATH; CI secret-scan is authoritative")
        return self.delegate([binary, "protect", "--staged", "--redact"])

    def cmd_pointer(self, _args: argparse.Namespace) -> int:
        script = FUTURE_GATES["pointer"]
        if not os.path.exists(script):
            return self.degrade(
                "check_pointer_integrity.py",
                "gate script lands in batch B2; CI pointer check is authoritative",
            )
        return self.delegate([self.python, script])

    def cmd_commit_msg(self, args: argparse.Namespace) -> int:
        script = FUTURE_GATES["commit-msg"]
        if not os.path.exists(script):
            return self.degrade(
                "check_commit_msg.py",
                "gate script lands in batch B3; commit message is still reviewed",
            )
        return self.delegate([self.python, script, args.message_file])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local pre-commit dispatcher with graceful degradation."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="refuse to degrade when a tool is missing (default: warn and pass)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ruff", help="incremental ruff gate (diff_gate.py --ruff)")
    sub.add_parser("mypy", help="mypy scope ratchet gate (mypy_gate.py)")
    sub.add_parser("placement", help="oversized-module guard (placement_guard.py)")
    sub.add_parser("gitleaks", help="gitleaks staged-secret scan")
    sub.add_parser("pointer", help="spec/ pointer integrity check (batch B2)")
    commit_msg = sub.add_parser("commit-msg", help="commit message check (batch B3)")
    commit_msg.add_argument("message_file", help="file with the commit message")
    return parser


HANDLERS = {
    "ruff": GateRunner.cmd_ruff,
    "mypy": GateRunner.cmd_mypy,
    "placement": GateRunner.cmd_placement,
    "gitleaks": GateRunner.cmd_gitleaks,
    "pointer": GateRunner.cmd_pointer,
    "commit-msg": GateRunner.cmd_commit_msg,
}


def main(
    argv: list[str] | None = None,
    *,
    strict: bool = False,
    runner=run,
    which=shutil.which,
    prober=probe_module,
    python: str | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    gate = GateRunner(
        strict=strict or args.strict,
        runner=runner,
        which=which,
        prober=prober,
        python=python,
    )
    handler = HANDLERS[args.command]
    return handler(gate, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

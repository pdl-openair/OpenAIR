#!/usr/bin/env python3
"""Oversized-module debt guard ("giant magnet" guard).

For every .py/.ts/.tsx file changed by this diff: if the file had MORE THAN
``--threshold`` (default 800) lines at the merge base AND did not shrink
(net change >= 0), block it. Pure deletions and extraction refactors
(net negative) always pass - the guard rewards paying down debt.

Exemptions live in ``spec/enforcement/placement-exemptions.yml``
(path/reason/review-date/added-by; zero exemptions in the first round). A
malformed exemption file is a hard error. An exemption whose review-date is
in the past (or unparsable) is a violation - fail-closed (④F7).

Rename detection (``--name-status -M``): a renamed oversized module is
judged against the OLD path's line count at the merge base, so
``git mv big.py huge.py`` + growth cannot escape the guard (④F3).

``--check-file <path> --lines <n>`` checks a single file with the given
current line count (for unit tests and canaries).

Exit codes: 0 = pass, 1 = violation, 2 = usage/config/env error (git
failures - e.g. an unresolvable base ref - are 2, never 1; M6).

Pure Python >= 3.10, standard library only. The exemption file uses a fixed,
restricted YAML subset parsed by hand (no PyYAML dependency).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

DEFAULT_BASE = "origin/test"
DEFAULT_THRESHOLD = 800
DEFAULT_EXEMPTIONS = "spec/enforcement/placement-exemptions.yml"
GUARDED_SUFFIXES = (".py", ".ts", ".tsx")
EXEMPTION_KEYS = ("path", "reason", "review-date", "added-by")


# ---------------------------------------------------------------------------
# exemption parsing (pure; restricted YAML subset)
# ---------------------------------------------------------------------------


def _clean_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_exemptions(text: str) -> list[dict[str, str]]:
    """Parse the restricted exemptions YAML subset.

    Supported grammar (comments with '#', blank lines, one entry per '- ' item):
        exemptions: []
        exemptions:
          - path: <value>
            reason: <value>
            review-date: <value>
            added-by: <value>

    Raises ValueError on anything else. Values keep everything after the first
    ':' (reasons may contain colons); no inline comments after values.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    if not lines:
        raise ValueError("exemptions file is empty")
    header = lines[0]
    if header == "exemptions:":
        body = lines[1:]
    elif header == "exemptions: []":
        body = []
        if len(lines) > 1:
            raise ValueError(f"unexpected content after 'exemptions: []': {lines[1]!r}")
    else:
        raise ValueError(f"first significant line must be 'exemptions:', got {lines[0]!r}")
    def finalize(entry: dict[str, str] | None) -> None:
        if entry is None:
            return
        missing = [key for key in EXEMPTION_KEYS if not entry.get(key)]
        if missing:
            raise ValueError(
                f"exemption for {entry.get('path', '<no path>')} missing keys: {missing}"
            )
        entries.append(entry)

    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in body:
        if raw.startswith("- "):
            finalize(current)
            current = {}
            raw = raw[2:]
        if current is None:
            raise ValueError(f"expected an entry starting with '- ', got {raw!r}")
        key, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"entry line without 'key: value' form: {raw!r}")
        key = key.strip()
        if key not in EXEMPTION_KEYS:
            raise ValueError(f"unknown key {key!r} (allowed: {', '.join(EXEMPTION_KEYS)})")
        current[key] = _clean_value(value)
    finalize(current)
    return entries


def load_exemptions(path: str) -> list[dict[str, str]]:
    """Load and validate the exemption file; ValueError if malformed."""
    with open(path, encoding="utf-8") as handle:
        return parse_exemptions(handle.read())


def stale_exemptions(entries: list[dict[str, str]], today=None) -> list[str]:
    """④F7：review-date 早于今日（或不可解析）的豁免 → 过期消息列表。

    fail-closed：日期格式错同样算过期（豁免文件宣称的复审纪律必须可执行）。
    """
    today = today or _dt.date.today()
    messages: list[str] = []
    for entry in entries:
        raw = entry.get("review-date", "")
        try:
            review_day = _dt.date.fromisoformat(raw)
        except ValueError:
            messages.append(
                f"{entry.get('path', '<no path>')}: review-date {raw!r} 不是合法 "
                f"YYYY-MM-DD 日期——豁免无效（续期=更新 review-date 并重新评审）"
            )
            continue
        if review_day < today:
            messages.append(
                f"{entry.get('path', '<no path>')}: review-date {raw} 早于今日 "
                f"{today.isoformat()}——豁免过期（续期=更新 review-date 并重新评审；"
                "不续期则删除该豁免并按守卫要求先做净缩减）"
            )
    return messages


# ---------------------------------------------------------------------------
# placement decision (pure)
# ---------------------------------------------------------------------------


def is_guarded_file(path: str) -> bool:
    return path.endswith(GUARDED_SUFFIXES)


def evaluate_placement(
    path: str,
    base_lines: int,
    current_lines: int,
    threshold: int,
    exempted_paths: set[str],
) -> str | None:
    """Return a violation message for the file, or None when allowed.

    Blocked only when: guarded extension, not exempted, oversized at the
    merge base (> threshold), and did not shrink (current >= base).
    """
    if not is_guarded_file(path):
        return None
    if path in exempted_paths:
        return None
    if base_lines <= threshold:
        return None
    if current_lines < base_lines:
        return None
    return (
        f"{path}: {base_lines} -> {current_lines} lines "
        f"(>= {base_lines}, no shrink; threshold {threshold})"
    )


# ---------------------------------------------------------------------------
# git plumbing (self-contained)
# ---------------------------------------------------------------------------


def has_head(cwd: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def merge_base(base: str, cwd: str) -> str:
    """Best-effort merge base; falls back to the base ref itself."""
    proc = subprocess.run(
        ["git", "merge-base", base, "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    proc = subprocess.run(
        ["git", "rev-parse", base],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


def changed_files(base: str, cwd: str) -> list[tuple[str, str, str]]:
    """(status, old_path, new_path) per changed file (④F3: -M rename detection).

    Rename/copy records carry the OLD path as the baseline anchor; every
    other status has old == new.
    """
    sep = f"{base}...HEAD" if has_head(cwd) else base
    proc = subprocess.run(
        ["git", "diff", "--no-color", "--name-status", "-M", "-z", sep],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    fields = [p.replace("\\", "/") for p in proc.stdout.split("\0") if p]
    records: list[tuple[str, str, str]] = []
    i = 0
    while i < len(fields):
        status = fields[i]
        if status.upper().startswith(("R", "C")) and i + 2 < len(fields):
            records.append((status, fields[i + 1], fields[i + 2]))
            i += 3
        elif i + 1 < len(fields):
            records.append((status, fields[i + 1], fields[i + 1]))
            i += 2
        else:
            break
    return records


def base_line_count(ref: str, path: str, cwd: str) -> int:
    """Line count of the file at ref; 0 when the file did not exist there."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=cwd,
        capture_output=True,
    )
    if proc.returncode != 0:
        return 0
    return len(proc.stdout.decode("utf-8", errors="replace").splitlines())


def current_line_count(root: str, path: str) -> int:
    """Line count of the working-tree file; 0 when deleted."""
    full = os.path.join(root, *path.split("/"))
    try:
        with open(full, encoding="utf-8", errors="replace") as handle:
            return len(handle.read().splitlines())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Oversized-module guard: >threshold lines at base and no shrink -> block."
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="git ref to diff against")
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help="line count threshold at the merge base (frozen: 800)",
    )
    parser.add_argument(
        "--exemptions",
        default=DEFAULT_EXEMPTIONS,
        help="exemption file path (repo-relative)",
    )
    parser.add_argument(
        "--root", default=None, help="repo root (default: parent of this script's dir)"
    )
    parser.add_argument(
        "--check-file",
        default=None,
        help="check a single file (canary/test mode) instead of the diff",
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=None,
        help="current line count for --check-file",
    )
    return parser


def default_root() -> str:
    """Repo root default: this script lives at <root>/scripts/gates/."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve_base(base: str, root: str) -> str:
    """merge_base with M6 fail-closed handling: any git failure -> EXIT_ERROR."""
    try:
        return merge_base(base, root)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(
            f"[placement] cannot resolve base ref {base!r} (git error; expected "
            f"e.g. origin/test or a commit sha): {exc}",
            file=sys.stderr,
        )
        raise _BaseResolutionError from exc


class _BaseResolutionError(Exception):
    """Internal marker: base ref unresolvable (already reported to stderr)."""


def _run_check_file(args: argparse.Namespace, root: str, exempted: set[str]) -> int:
    """--check-file 模式（canary/单测形态）单独成函数，压住 main 的 C901。"""
    if args.lines is None:
        print("[placement] --check-file requires --lines", file=sys.stderr)
        return EXIT_ERROR
    try:
        ref = _resolve_base(args.base, root)
    except _BaseResolutionError:
        return EXIT_ERROR
    base_lines = base_line_count(ref, args.check_file, root)
    message = evaluate_placement(
        args.check_file, base_lines, args.lines, args.threshold, exempted
    )
    if message:
        print(f"[placement] BLOCKED: {message}", file=sys.stderr)
        return EXIT_VIOLATION
    print(
        f"[placement] PASS: {args.check_file} base={base_lines} "
        f"now={args.lines} threshold={args.threshold}"
    )
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root or default_root())
    exemptions_path = os.path.join(root, args.exemptions)
    try:
        entries = load_exemptions(exemptions_path)
    except OSError as exc:
        print(f"[placement] cannot read exemption file {exemptions_path}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print(f"[placement] malformed exemption file: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # ④F7：豁免过期校验（fail-closed——过期即违规，早于任何模式判定）
    stale = stale_exemptions(entries)
    for message in stale:
        print(f"[placement] BLOCKED: exemption expired: {message}", file=sys.stderr)
    if stale:
        return EXIT_VIOLATION

    exempted = {entry["path"] for entry in entries}
    for entry in entries:
        print(
            f"[placement] exemption active: {entry['path']} "
            f"(review-date {entry['review-date']})"
        )

    if args.check_file is not None:
        return _run_check_file(args, root, exempted)

    try:
        ref = _resolve_base(args.base, root)
        records = changed_files(args.base, root)
    except _BaseResolutionError:
        return EXIT_ERROR
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"[placement] git error while enumerating changes: {exc}", file=sys.stderr)
        return EXIT_ERROR
    violations = []
    checked = 0
    for status, old_path, new_path in records:
        if not is_guarded_file(new_path):
            continue
        checked += 1
        # ④F3：R/C 记录取旧路径在 base 的行数为基线；其余状态新路径即基线
        baseline_path = old_path if status.upper().startswith(("R", "C")) else new_path
        base_lines = base_line_count(ref, baseline_path, root)
        current_lines = current_line_count(root, new_path)
        message = evaluate_placement(
            new_path, base_lines, current_lines, args.threshold, exempted
        )
        if message:
            violations.append(message)
    for message in violations:
        print(f"[placement] BLOCKED: {message}", file=sys.stderr)
    print(
        f"[placement] checked {checked} guarded file(s), "
        f"{len(violations)} violation(s) (threshold {args.threshold})"
    )
    return EXIT_VIOLATION if violations else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

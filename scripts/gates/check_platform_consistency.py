#!/usr/bin/env python3
"""Platform consistency checks for Linux/Windows contributors.

Checks tracked files (not the working tree) so local editor settings cannot
hide drift:

* line endings: no CRLF except Windows-only script/config suffixes
* filename case collisions: two tracked paths differing only by case
* executable bits: ``*.sh`` tracked as 100755, other files not executable
* bash syntax: ``bash -n`` on every tracked ``*.sh`` (skipped when bash is
  unavailable, with a warning; CI always has bash)

Exit codes: 0 = pass, 1 = violation, 2 = environment error (git/bash missing).
Pure Python >= 3.10, standard library only.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

CRLF_ALLOWED_SUFFIXES = (".bat", ".cmd", ".ps1", ".psm1", ".psd1")
SHELL_SUFFIX = ".sh"
MAX_SCAN_BYTES = 2 * 1024 * 1024


def git_bytes(root: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.decode("utf-8", errors="replace").strip())
    return proc.stdout


def tracked_files(root: Path) -> list[str]:
    raw = git_bytes(root, "ls-files", "-z")
    return [item for item in raw.decode("utf-8", errors="replace").split("\0") if item]


def tracked_modes(root: Path) -> dict[str, str]:
    raw = git_bytes(root, "ls-files", "-s", "-z")
    modes: dict[str, str] = {}
    for entry in raw.decode("utf-8", errors="replace").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) >= 2:
            modes[path] = parts[0]
    return modes


def has_crlf(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return b"\r\n" in data[:MAX_SCAN_BYTES]


def is_binary(path: Path) -> bool:
    try:
        return b"\0" in path.read_bytes()[:8192]
    except OSError:
        return True


def check_crlf(root: Path, files: list[str]) -> list[str]:
    problems = []
    for name in files:
        if name.endswith(CRLF_ALLOWED_SUFFIXES):
            continue
        path = root / name
        if path.is_file() and not is_binary(path) and has_crlf(path):
            problems.append(f"CRLF line endings: {name}")
    return problems


def check_case_collisions(files: list[str]) -> list[str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for name in files:
        grouped[name.casefold()].append(name)
    return [
        "filename case collision: " + ", ".join(sorted(names))
        for names in grouped.values()
        if len(names) > 1
    ]


def check_modes(files: list[str], modes: dict[str, str]) -> list[str]:
    problems = []
    for name in files:
        mode = modes.get(name)
        if mode is None:
            continue
        if name.endswith(SHELL_SUFFIX) and mode != "100755":
            problems.append(f"shell script not executable ({mode}): {name}")
        if not name.endswith(SHELL_SUFFIX) and mode == "100755":
            problems.append(f"unexpected executable bit: {name}")
    return problems


def check_bash_syntax(root: Path, files: list[str]) -> list[str]:
    bash = shutil.which("bash")
    if bash is None:
        print("[platform] warning: bash not found; skipping syntax check", file=sys.stderr)
        return []
    problems = []
    for name in files:
        if not name.endswith(SHELL_SUFFIX):
            continue
        proc = subprocess.run(
            [bash, "-n", str(root / name)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            problems.append(f"bash syntax error: {name}: {detail[0] if detail else 'unknown'}")
    return problems


def changed_files(root: Path, base: str) -> list[str]:
    raw = git_bytes(
        root, "diff", "--name-only", "-z", "--diff-filter=ACMR", f"{base}...HEAD"
    )
    return [item for item in raw.decode("utf-8", errors="replace").split("\0") if item]


def run_checks(root: Path, base: str | None, check_all: bool) -> list[str]:
    files = tracked_files(root)
    modes = tracked_modes(root)
    scope = files if check_all or not base else changed_files(root, base)
    problems = []
    problems.extend(check_crlf(root, scope))
    problems.extend(check_case_collisions(files))
    problems.extend(check_modes(scope, modes))
    problems.extend(check_bash_syntax(root, scope))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--base",
        default=None,
        help="git ref to diff against; only changed files are checked when set",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="check every tracked file instead of only changed files",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        problems = run_checks(root, args.base, args.all)
    except OSError as exc:
        print(f"[platform] environment error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    for problem in problems:
        print(f"[platform] {problem}")
    print(f"[platform] {len(problems)} problem(s)")
    return EXIT_VIOLATION if problems else EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())

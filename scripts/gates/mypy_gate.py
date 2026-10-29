#!/usr/bin/env python3
"""mypy scope ratchet gate.

``spec/enforcement/mypy-scope.txt`` lists files EXEMPT from mypy (the frozen
baseline of files that already had errors). Three checks:

1. Out-of-scope-list product files must be mypy-clean.
2. Ratchet: a diff may only REMOVE entries from the list, never add them
   (the initial freeze of the file itself is allowed - bootstrap).
3. Zombie check: every listed path must still exist on disk.

``--freeze`` (re)generates the scope file by running mypy over the whole
in-scope tree and listing every file that reports an error.

The base ref is validated up front (``git rev-parse --verify --quiet``): an
unresolvable ref is a hard env error (exit 2), never a silent bootstrap.
All script paths (root / config / scope) anchor on this file's location,
and ``--python`` is absolutized before spawning mypy with cwd=run_root
(a relative interpreter path would stop resolving there).

Exit codes: 0 = pass, 1 = gate violation, 2 = usage/config/env error.

Pure Python >= 3.10, standard library only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

DEFAULT_BASE = "origin/test"
DEFAULT_SCOPE_FILE = "spec/enforcement/mypy-scope.txt"

_ERROR_LINE = re.compile(r"^(?P<path>[^:\n]+):(?:(?P<line>\d+): )?error:")


# ---------------------------------------------------------------------------
# scope discovery: repo-relative file -> (run root, path relative to it)
# ---------------------------------------------------------------------------


def discover_package_roots(root: str) -> list[tuple[str, str]]:
    """(run_root, source_subdir) pairs for the in-scope python packages.

    ``run_root`` is the import base used as mypy's cwd (module resolution);
    ``source_subdir`` is the tree walked for in-scope files, relative to the
    run root. ``services/api`` + ``app``; every python package under
    ``packages/*`` (src-layout: run root is the src dir itself; name-layout:
    run root is the package parent, subdir is the package name).
    """
    pairs = [("services/api", "app")]
    packages_dir = os.path.join(root, "packages")
    if not os.path.isdir(packages_dir):
        return pairs
    for name in sorted(os.listdir(packages_dir)):
        pkg_dir = os.path.join(packages_dir, name)
        if os.path.isdir(os.path.join(pkg_dir, "src")):
            pairs.append((f"packages/{name}/src", "."))
        elif os.path.isdir(os.path.join(pkg_dir, name)):
            pairs.append((f"packages/{name}", name))
    return pairs


def discover_scope_files(root: str) -> dict[str, list[str]]:
    """Map each run root to its in-scope ``*.py`` files (run-root relative)."""
    mapping: dict[str, list[str]] = {}
    for run_root, subdir in discover_package_roots(root):
        abs_root = os.path.join(root, run_root)
        base = abs_root if subdir == "." else os.path.join(abs_root, subdir)
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    rel = os.path.relpath(os.path.join(dirpath, filename), abs_root)
                    found.append(rel.replace("\\", "/"))
        if found:
            mapping[run_root] = found
    return mapping


def repo_relative(run_root: str, rel_path: str) -> str:
    """Compose a repo-relative forward-slash path from a run root + rel path."""
    rel = rel_path.replace("\\", "/")
    return f"{run_root}/{rel}" if run_root != "." else rel


# ---------------------------------------------------------------------------
# scope file parsing (pure)
# ---------------------------------------------------------------------------


def strip_entry(line: str) -> str:
    """Strip a '# comment' tail and whitespace from a scope entry line."""
    return line.split("#", 1)[0].strip()


def parse_scope_text(text: str) -> list[str]:
    """Parse the exemption list: one repo-relative path per line, '#' comments.

    Pure function; raises ValueError on malformed entries.
    """
    entries: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        entry = strip_entry(raw)
        if not entry:
            continue
        if any(ch.isspace() for ch in entry):
            raise ValueError(f"scope entry with whitespace on line {number}: {raw!r}")
        entries.append(entry.replace("\\", "/"))
    return entries


def added_entries_from_diff(diff_text: str) -> set[str]:
    """Entries appearing on added (+) diff lines; deletions are ignored."""
    added: set[str] = set()
    for raw in diff_text.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        entry = strip_entry(raw[1:])
        if entry:
            added.add(entry.replace("\\", "/"))
    return added


def find_zombie_entries(entries: list[str], root: str) -> list[str]:
    """Listed paths that no longer exist on disk (dead exemptions)."""
    return [entry for entry in entries if not os.path.exists(os.path.join(root, entry))]


def split_checked(all_files: dict[str, list[str]], exempted: set[str]):
    """Split discovered files into (checked, exempt) as run-root rel paths."""
    checked: dict[str, list[str]] = {}
    exempt_count = 0
    for run_root, files in all_files.items():
        keep = []
        for rel in files:
            if repo_relative(run_root, rel) in exempted:
                exempt_count += 1
            else:
                keep.append(rel)
        if keep:
            checked[run_root] = keep
    return checked, exempt_count


# ---------------------------------------------------------------------------
# mypy invocation
# ---------------------------------------------------------------------------


def run_mypy(
    python: str,
    root: str,
    run_root: str,
    rel_files: list[str],
    config_file: str,
) -> subprocess.CompletedProcess:
    """Run mypy on rel_files with cwd=run_root (module resolution base)."""
    # ⑤ 根治：解释器路径先绝对化——子进程 cwd 换到 run_root 后，相对 --python
    # 路径不再解析（CI 曾以 workflow 侧 $PWD 规避，现为脚本内双保险之一）。
    python = os.path.abspath(python)
    argv = [
        python,
        "-m",
        "mypy",
        "--config-file",
        os.path.abspath(config_file),
        "--follow-imports=silent",
        "--no-error-summary",
        *rel_files,
    ]
    return subprocess.run(
        argv,
        cwd=os.path.join(root, run_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_mypy_errors(output: str, run_root: str) -> dict[str, list[str]]:
    """Group mypy error lines per repo-relative file."""
    errors: dict[str, list[str]] = {}
    for line in output.splitlines():
        match = _ERROR_LINE.match(line)
        if not match:
            continue
        path = match.group("path").replace("\\", "/")
        errors.setdefault(repo_relative(run_root, path), []).append(line)
    return errors


# ---------------------------------------------------------------------------
# git plumbing (self-contained duplicate of diff_gate helpers)
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


def ref_exists(ref: str, cwd: str) -> bool:
    """True when git resolves ref to a commit (quiet; H3 entry validation)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def scope_diff(base: str, cwd: str, scope_file: str) -> str:
    """Diff of the scope file between base and HEAD/working tree."""
    sep = f"{base}...HEAD" if has_head(cwd) else base
    proc = subprocess.run(
        ["git", "diff", "--no-color", "--unified=0", sep, "--", scope_file],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout


def file_exists_at_ref(ref: str, path: str, cwd: str) -> bool:
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def head_commit(cwd: str) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# gate checks
# ---------------------------------------------------------------------------


def check_out_of_scope(
    python: str, root: str, checked: dict[str, list[str]], config_file: str
) -> list[str]:
    """Run mypy on every non-exempt file; return violation message lines."""
    problems: list[str] = []
    for run_root, rel_files in sorted(checked.items()):
        proc = run_mypy(python, root, run_root, rel_files, config_file)
        if proc.returncode not in (0, 1):
            problems.append(
                f"mypy crashed on {run_root} (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:500]}"
            )
            continue
        for _path, lines in sorted(parse_mypy_errors(proc.stdout, run_root).items()):
            for line in lines:
                problems.append(f"out-of-scope mypy error: {line}")
    return problems


def check_ratchet(base: str, root: str, scope_file: str) -> tuple[list[str], str]:
    """Ratchet: the diff may only delete entries. Returns (problems, mode)."""
    if not file_exists_at_ref(base, scope_file, root):
        return [], "bootstrap (scope file absent at base; initial freeze)"
    diff_text = scope_diff(base, root, scope_file)
    added = added_entries_from_diff(diff_text)
    problems = [
        f"ratchet violation: scope entry ADDED in this diff: {entry}"
        for entry in sorted(added)
    ]
    return problems, "ratchet (file existed at base)"


def freeze(
    python: str, base: str, root: str, scope_file: str, config_file: str
) -> int:
    """Regenerate the scope file: every in-scope file with mypy errors enters."""
    all_files = discover_scope_files(root)
    error_files: set[str] = set()
    total = 0
    for run_root, rel_files in sorted(all_files.items()):
        total += len(rel_files)
        proc = run_mypy(python, root, run_root, rel_files, config_file)
        if proc.returncode not in (0, 1):
            print(
                f"[mypy_gate] mypy crashed on {run_root} (exit {proc.returncode}):",
                file=sys.stderr,
            )
            print((proc.stderr or proc.stdout)[:2000], file=sys.stderr)
            return EXIT_ERROR
        error_files.update(parse_mypy_errors(proc.stdout, run_root))
    try:
        head = head_commit(root)
    except subprocess.CalledProcessError as exc:
        print(f"[mypy_gate] cannot resolve HEAD: {exc}", file=sys.stderr)
        return EXIT_ERROR
    today = _dt.date.today().isoformat()
    lines = [
        "# mypy exemption scope - files EXEMPT from the mypy gate (ratchet:",
        "# entries may only be removed, never added).",
        f"# Generated: {today}",
        f"# Baseline commit: {head} (base ref: {base})",
        "# Command: python scripts/gates/mypy_gate.py --freeze"
        f" --base {base} --python <interpreter>",
        "",
        *sorted(error_files),
        "",
    ]
    os.makedirs(os.path.dirname(os.path.join(root, scope_file)), exist_ok=True)
    with open(os.path.join(root, scope_file), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    print(
        f"[mypy_gate] frozen scope: {len(error_files)} file(s) with errors "
        f"entered the exemption list out of {total} in-scope file(s)"
    )
    return EXIT_PASS


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="mypy scope ratchet gate (exemptions only shrink)."
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="git ref to diff against")
    parser.add_argument(
        "--root", default=None, help="repo root (default: parent of this script's dir)"
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter used to run mypy via -m (default: this interpreter)",
    )
    parser.add_argument(
        "--scope-file",
        default=DEFAULT_SCOPE_FILE,
        help="exemption list path (repo-relative)",
    )
    parser.add_argument(
        "--mypy-config",
        default=None,
        help="mypy config path (default: <root>/mypy.ini)",
    )
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="(re)generate the scope file from the current error baseline",
    )
    return parser


def default_root() -> str:
    """Repo root default, anchored on this file: <root>/scripts/gates/mypy_gate.py.

    cwd-independent (⑤)：从任意工作目录调用都指向同一仓库根。
    """
    return str(Path(__file__).resolve().parents[2])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root or default_root())
    # H3：入口先验证 base ref——无效 ref 是 env error（exit 2），
    # 绝不落入 bootstrap（"ref 有效但 scope 文件不存在"才是合法 bootstrap）。
    if not ref_exists(args.base, root):
        print(
            f"[mypy_gate] invalid base ref: {args.base!r} does not resolve in this "
            "repository (expected e.g. origin/test or a commit sha); refusing to "
            "fall back to bootstrap",
            file=sys.stderr,
        )
        return EXIT_ERROR
    # ⑤：config 相对路径锚定到 root（非调用方 cwd）
    if args.mypy_config:
        config_file = (
            args.mypy_config
            if os.path.isabs(args.mypy_config)
            else os.path.join(root, args.mypy_config)
        )
    else:
        config_file = os.path.join(root, "mypy.ini")
    if args.freeze:
        return freeze(args.python, args.base, root, args.scope_file, config_file)

    scope_path = os.path.join(root, args.scope_file)
    try:
        with open(scope_path, encoding="utf-8") as handle:
            entries = parse_scope_text(handle.read())
    except OSError as exc:
        print(
            f"[mypy_gate] scope file missing ({scope_path}); "
            f"run with --freeze first: {exc}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    except ValueError as exc:
        print(f"[mypy_gate] malformed scope file: {exc}", file=sys.stderr)
        return EXIT_ERROR

    exempted = set(entries)
    problems: list[str] = []

    zombies = find_zombie_entries(entries, root)
    problems.extend(f"zombie entry (file deleted): {z}" for z in sorted(zombies))

    ratchet_problems, mode = check_ratchet(args.base, root, args.scope_file)
    problems.extend(ratchet_problems)

    all_files = discover_scope_files(root)
    checked, exempt_count = split_checked(all_files, exempted)
    problems.extend(check_out_of_scope(args.python, root, checked, config_file))

    total_files = sum(len(files) for files in all_files.values())
    checked_count = sum(len(files) for files in checked.values())
    print(
        f"[mypy_gate] scope={len(entries)} exempt / {total_files} in-scope "
        f"({exempt_count} hit); checking {checked_count} file(s); mode={mode}"
    )
    for problem in problems:
        print(f"[mypy_gate] {problem}", file=sys.stderr)
    if problems:
        print(f"[mypy_gate] FAIL: {len(problems)} problem(s)", file=sys.stderr)
        return EXIT_VIOLATION
    print("[mypy_gate] PASS")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

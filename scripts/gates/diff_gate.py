#!/usr/bin/env python3
"""Incremental line-level quality gate.

Only reports tool findings whose (file, line) falls inside the set of lines
added by this change relative to ``--base``. Existing-code findings never fail
the gate (hard constraint: zero red on legacy code).

Modes (mutually exclusive):
  --ruff       run ``ruff check`` (via ``--python`` interpreter) and filter.
  --eslint     run eslint through pnpm in ``--frontend-dir`` and filter.
  --prettier   run ``prettier --check`` through pnpm and filter (file-level).
  --coverage   evaluate diff-coverage of product code from pytest-cov JSON.

Exit codes: 0 = pass, 1 = violations on added lines, 2 = usage/env error.

Pure Python >= 3.10, standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

DEFAULT_BASE = "origin/test"
DEFAULT_FRONTEND_DIR = "apps/web"
DEFAULT_COVERAGE_FILE = "coverage.json"
DEFAULT_COVERAGE_THRESHOLD = 50.0

# Product-code location rules for the coverage denominator (repo-relative,
# forward slashes). Test files are excluded separately by is_test_file().
# Layouts: services/api/app/**, services/worker/**, packages/<name>/src/**
# (conventional src layout) and packages/<name>/<name>/** (this repo's
# llm_core layout).
PRODUCT_PREFIXES = ("services/api/app/", "services/worker/")


@dataclass(frozen=True)
class Violation:
    """One tool finding. ``line`` None means a file-level finding."""

    file: str
    line: int | None
    code: str
    message: str


@dataclass
class DiffLines:
    """Added lines per file plus the set of newly created files."""

    added: dict[str, set[int]] = field(default_factory=dict)
    new_files: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------


def run_cmd(argv: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a subprocess, returning the completed process (check=True)."""
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def has_head(cwd: str) -> bool:
    """True when the repository has at least one commit on HEAD."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def read_diff(base: str, cwd: str) -> str:
    """Unified diff of added lines vs base (three-dot, working-tree fallback)."""
    if has_head(cwd):
        argv = ["git", "diff", "--no-color", "--unified=0", f"{base}...HEAD"]
    else:
        # No HEAD to compare against: diff base against the working tree.
        argv = ["git", "diff", "--no-color", "--unified=0", base]
    return run_cmd(argv, cwd).stdout


# ---------------------------------------------------------------------------
# diff parsing (pure)
# ---------------------------------------------------------------------------


def unquote_git_path(token: str) -> str:
    """Decode a git-quoted path (C style with octal UTF-8 escapes)."""
    if not (token.startswith('"') and token.endswith('"') and len(token) >= 2):
        return token
    body = token[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.extend(ch.encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= len(body):
            break
        esc = body[i]
        mapped = {"n": b"\n", "t": b"\t", "r": b"\r", '"': b'"', "\\": b"\\"}
        if esc in mapped:
            out.extend(mapped[esc])
            i += 1
        elif esc.isdigit():
            octal = body[i : i + 3]
            try:
                out.append(int(octal, 8))
            except ValueError:
                out.extend(esc.encode("utf-8"))
            i += 3
        else:
            out.extend(esc.encode("utf-8"))
            i += 1
    return out.decode("utf-8", errors="replace")


def parse_hunk_new_range(header: str) -> tuple[int, int] | None:
    """Extract ``(start, count)`` of the new side from a @@ header."""
    marker = header.find("@@")
    if marker == -1:
        return None
    rest = header[marker + 2 :]
    end = rest.find("@@")
    parts = rest[:end].split() if end != -1 else rest.split()
    new_part = None
    seen_plus = False
    for part in parts:
        if part.startswith("+"):
            seen_plus = True
            new_part = part[1:]
            break
    if not seen_plus or new_part is None:
        return None
    if ":" in new_part:
        new_part = new_part.split(":", 1)[0]
    start_str, _, count_str = new_part.partition(",")
    try:
        start = int(start_str)
        count = int(count_str) if count_str else 1
    except ValueError:
        return None
    return start, count


def parse_diff(diff_text: str) -> DiffLines:
    """Parse a unified diff into per-file added line numbers + new files.

    Pure function: input is the diff text, output is the added-line map.
    Correct for any unified-diff context width (tracks '+' lines inside each
    hunk body instead of trusting the hunk range).
    """
    result = DiffLines()
    current: str | None = None
    is_new = False
    in_hunk = False
    new_line = 0
    for raw_line in diff_text.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("+++ "):
            token = line[4:].strip()
            path = unquote_git_path(token)
            in_hunk = False
            if path == "/dev/null":
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current = path.replace("\\", "/")
            result.added.setdefault(current, set())
            if is_new:
                result.new_files.add(current)
            is_new = False
            continue
        if line.startswith("new file mode"):
            is_new = True
            continue
        if line.startswith("--- "):
            token = line[4:].strip()
            if unquote_git_path(token) == "/dev/null":
                is_new = True
            continue
        if line.startswith("@@"):
            in_hunk = current is not None
            parsed = parse_hunk_new_range(line)
            new_line = parsed[0] if parsed else 1
            continue
        if not in_hunk or current is None:
            continue
        if line.startswith("+"):
            result.added[current].add(new_line)
            new_line += 1
        elif line.startswith("-"):
            continue
        elif line.startswith("\\"):
            continue
        else:
            new_line += 1
    return result


def filter_violations(
    violations: list[Violation], diff: DiffLines
) -> list[Violation]:
    """Keep only findings on added lines (or file-level findings in new files)."""
    kept: list[Violation] = []
    for violation in violations:
        added = diff.added.get(violation.file)
        if violation.line is None:
            if violation.file in diff.new_files:
                kept.append(violation)
            continue
        if added and violation.line in added:
            kept.append(violation)
    return kept


def normalize_repo_path(path: str, root: str) -> str:
    """Make a tool-reported path repo-relative with forward slashes."""
    if os.path.isabs(path):
        try:
            path = os.path.relpath(path, root)
        except ValueError:
            return path.replace("\\", "/")
    return path.replace("\\", "/")


# ---------------------------------------------------------------------------
# ruff mode
# ---------------------------------------------------------------------------


def parse_ruff_json(payload: str, root: str) -> list[Violation]:
    """Convert ruff ``--output-format json`` output to Violations."""
    try:
        entries = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot parse ruff JSON output: {exc}") from exc
    violations: list[Violation] = []
    for entry in entries:
        location = entry.get("location") or {}
        line = location.get("row") or entry.get("line")
        violations.append(
            Violation(
                file=normalize_repo_path(str(entry.get("filename", "")), root),
                line=int(line) if line is not None else None,
                code=str(entry.get("code", "RUFF")),
                message=str(entry.get("message", "")),
            )
        )
    return violations


def run_ruff_gate(python: str, base: str, root: str) -> int:
    """Run ruff over the repo and report only added-line violations."""
    proc = subprocess.run(
        [python, "-m", "ruff", "check", "--output-format", "json", "."],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode not in (0, 1):
        print(f"[diff_gate] ruff failed (exit {proc.returncode}):", file=sys.stderr)
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return EXIT_ERROR
    violations = parse_ruff_json(proc.stdout or "[]", root)
    diff = parse_diff(read_diff(base, root))
    kept = filter_violations(violations, diff)
    for item in kept:
        print(f"{item.file}:{item.line}: [{item.code}] {item.message}")
    print(
        f"[diff_gate] ruff: {len(kept)} violation(s) on added lines "
        f"({len(violations)} total, filtered)"
    )
    return EXIT_VIOLATION if kept else EXIT_PASS


# ---------------------------------------------------------------------------
# eslint / prettier modes
# ---------------------------------------------------------------------------


def parse_eslint_json(payload: str, root: str) -> list[Violation]:
    """Convert eslint ``--format json`` output to Violations."""
    try:
        entries = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot parse eslint JSON output: {exc}") from exc
    violations: list[Violation] = []
    for entry in entries:
        path = normalize_repo_path(str(entry.get("filePath", "")), root)
        for message in entry.get("messages", []):
            line = message.get("line")
            violations.append(
                Violation(
                    file=path,
                    line=int(line) if line is not None else None,
                    code=str(message.get("ruleId") or "eslint"),
                    message=str(message.get("message", "")),
                )
            )
    return violations


def run_pnpm(frontend_dir: str, root: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run ``pnpm --dir <frontend_dir> exec <args>`` from the repo root."""
    return subprocess.run(
        ["pnpm", "--dir", frontend_dir, "exec", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def run_eslint_gate(base: str, root: str, frontend_dir: str) -> int:
    proc = run_pnpm(frontend_dir, root, ["eslint", "--format", "json", "."])
    if proc.returncode not in (0, 1):
        print(f"[diff_gate] eslint failed (exit {proc.returncode}):", file=sys.stderr)
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return EXIT_ERROR
    violations = parse_eslint_json(proc.stdout or "[]", root)
    diff = parse_diff(read_diff(base, root))
    kept = filter_violations(violations, diff)
    for item in kept:
        print(f"{item.file}:{item.line}: [{item.code}] {item.message}")
    print(
        f"[diff_gate] eslint: {len(kept)} violation(s) on added lines "
        f"({len(violations)} total, filtered)"
    )
    return EXIT_VIOLATION if kept else EXIT_PASS


def parse_prettier_output(payload: str, frontend_dir: str) -> list[Violation]:
    """File-level violations from ``prettier --check`` [warn] lines."""
    violations: list[Violation] = []
    prefix = "[warn] "
    for line in payload.splitlines():
        if not line.startswith(prefix):
            continue
        path = line[len(prefix) :].strip()
        if not path or path.startswith("Code style"):
            continue
        joined = f"{frontend_dir}/{path}".replace("\\", "/")
        violations.append(
            Violation(file=joined, line=None, code="prettier", message="file not formatted")
        )
    return violations


def filter_file_level(violations: list[Violation], diff: DiffLines) -> list[Violation]:
    """Keep file-level findings for files touched by the diff (any added line)."""
    kept = []
    for violation in violations:
        if violation.file in diff.new_files or diff.added.get(violation.file):
            kept.append(violation)
    return kept


def run_prettier_gate(base: str, root: str, frontend_dir: str) -> int:
    proc = run_pnpm(frontend_dir, root, ["prettier", "--check", "."])
    if proc.returncode not in (0, 1):
        print(
            f"[diff_gate] prettier failed (exit {proc.returncode}):", file=sys.stderr
        )
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return EXIT_ERROR
    violations = parse_prettier_output(proc.stdout, frontend_dir)
    diff = parse_diff(read_diff(base, root))
    kept = filter_file_level(violations, diff)
    for item in kept:
        print(f"{item.file}: [{item.code}] {item.message}")
    print(
        f"[diff_gate] prettier: {len(kept)} unformatted file(s) touched "
        f"({len(violations)} total, filtered)"
    )
    return EXIT_VIOLATION if kept else EXIT_PASS


# ---------------------------------------------------------------------------
# coverage mode
# ---------------------------------------------------------------------------


def is_test_file(path: str) -> bool:
    """True for test files excluded from the coverage denominator."""
    parts = path.split("/")
    if "tests" in parts:
        return True
    name = parts[-1] if parts else path
    return name.startswith("test_")


def is_product_code(path: str) -> bool:
    """True when path is product python code (test files never count)."""
    if is_test_file(path) or not path.endswith(".py"):
        return False
    if path.startswith(PRODUCT_PREFIXES):
        return True
    parts = path.split("/")
    if len(parts) >= 4 and parts[0] == "packages":
        # packages/<name>/src/** (conventional) or packages/<name>/<name>/**
        return parts[2] == "src" or parts[2] == parts[1]
    return False


@dataclass
class CoverageStats:
    """Diff-coverage aggregate over product files touched by the diff."""

    executable: int = 0
    covered: int = 0
    files: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        return (self.covered / self.executable) if self.executable else 1.0


def resolve_coverage_key(repo_path: str, coverage_files: dict[str, dict]) -> str | None:
    """Match a repo-relative product path to a coverage.json key.

    coverage.json keys are relative to the directory pytest ran in, so try the
    known package roots as prefixes.
    """
    if repo_path in coverage_files:
        return repo_path
    for prefix in ("services/api/", "packages/llm_core/"):
        if repo_path.startswith(prefix):
            key = repo_path[len(prefix) :]
            if key in coverage_files:
                return key
    return None


def compute_diff_coverage(
    diff: DiffLines, coverage_data: dict
) -> CoverageStats:
    """Diff-coverage of product code: added executable lines vs covered ones.

    Pure function. Files absent from coverage data count all added lines as
    missed (nothing exercised them).
    """
    stats = CoverageStats()
    coverage_files = {
        key.replace("\\", "/"): value
        for key, value in coverage_data.get("files", {}).items()
    }
    for path, added in sorted(diff.added.items()):
        if not is_product_code(path):
            continue
        key = resolve_coverage_key(path, coverage_files)
        entry = coverage_files.get(key) if key else None
        if entry is None:
            executable, covered = len(added), 0
        else:
            known = set(entry.get("executed_lines", [])) | set(
                entry.get("missing_lines", [])
            )
            executable = len(added & known)
            covered = len(added & set(entry.get("executed_lines", [])))
        if executable:
            stats.files[path] = (covered, executable)
        stats.executable += executable
        stats.covered += covered
    return stats


def run_coverage_gate(
    base: str, root: str, coverage_file: str, threshold: float
) -> int:
    diff = parse_diff(read_diff(base, root))
    try:
        with open(coverage_file, encoding="utf-8") as handle:
            coverage_data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[diff_gate] cannot read coverage file {coverage_file}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    stats = compute_diff_coverage(diff, coverage_data)
    percent = stats.ratio * 100.0
    for path, (covered, executable) in sorted(stats.files.items()):
        file_pct = (covered / executable) * 100.0 if executable else 100.0
        print(f"[diff_gate] coverage {path}: {covered}/{executable} ({file_pct:.1f}%)")
    print(
        f"[diff_gate] diff-coverage: {stats.covered}/{stats.executable} lines "
        f"= {percent:.1f}% (threshold {threshold:.1f}%)"
    )
    if stats.executable and percent < threshold:
        print("[diff_gate] FAIL: diff-coverage below threshold", file=sys.stderr)
        return EXIT_VIOLATION
    return EXIT_PASS


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Incremental line-level gate (ruff/eslint/prettier/coverage)."
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="git ref to diff against")
    parser.add_argument(
        "--root", default=None, help="repo root (default: parent of this script's dir)"
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter used to run ruff (default: this interpreter)",
    )
    parser.add_argument(
        "--frontend-dir",
        default=DEFAULT_FRONTEND_DIR,
        help="frontend directory for eslint/prettier (repo-relative)",
    )
    parser.add_argument(
        "--coverage-file",
        default=DEFAULT_COVERAGE_FILE,
        help="pytest-cov JSON report path (coverage mode)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_COVERAGE_THRESHOLD,
        help="diff-coverage threshold in percent (coverage mode)",
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--ruff", action="store_true", help="ruff check mode")
    modes.add_argument("--eslint", action="store_true", help="eslint mode")
    modes.add_argument("--prettier", action="store_true", help="prettier mode")
    modes.add_argument("--coverage", action="store_true", help="diff-coverage mode")
    return parser


def default_root() -> str:
    """Repo root default: this script lives at <root>/scripts/gates/."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root or default_root())
    try:
        if args.ruff:
            return run_ruff_gate(args.python, args.base, root)
        if args.eslint:
            return run_eslint_gate(args.base, root, args.frontend_dir)
        if args.prettier:
            return run_prettier_gate(args.base, root, args.frontend_dir)
        return run_coverage_gate(args.base, root, args.coverage_file, args.threshold)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr or exc.stdout or ""
        print(f"[diff_gate] git command failed: {' '.join(exc.cmd)}\n{detail}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print(f"[diff_gate] {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"[diff_gate] environment error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

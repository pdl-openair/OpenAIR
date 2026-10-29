#!/usr/bin/env python3
"""Pointer integrity gate: assert that repo-path references in rule/entry docs resolve.

Scan targets (design doc AI4SE Harness §7.10 / task spec B2 §2.7):
  - spec/**            (text rule files: .md/.yaml/.yml/.txt)
  - CLAUDE.md, AGENTS.md, README.md (root), CONTRIBUTING.md, docs/README.md
  - docs/dev_skills/*/SKILL.md

Extraction:
  - Markdown link targets `](target)` — resolved relative to the containing file
    (URL-%XX unquoted, trailing #anchor stripped). External schemes and pure
    anchors are ignored.
  - Backtick spans containing a repo-rooted path (docs/, spec/, scripts/,
    verification/, services/, apps/, packages/, skills/ ...). Glob/placeholder
    patterns and URLs are ignored.

Assertion: every extracted path exists (file or directory). Exit 0 when clean,
1 when any pointer is broken. `--fix-report` prints a repair hint per finding
(it never edits files).

Pure standard library; Windows-safe (UTF-8, Chinese filenames).
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Backtick paths must be rooted at one of these repo prefixes (task spec B2 §2.7).
REPO_PREFIXES = (
    "docs/",
    "spec/",
    "scripts/",
    "verification/",
    "services/",
    "apps/",
    "packages/",
    "skills/",
)

SPEC_TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".txt"}

EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

# Inline code span: content has no backtick and no newline.
BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# Markdown link target: ](target) — target stops at whitespace or closing paren.
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")

# Characters marking glob/placeholder patterns rather than literal paths.
PATTERN_CHARS = set("*?{}<>$")

# Punctuation that may trail a path token inside prose (CJK and ASCII).
TRAILING_PUNCT = "。，、；：）（】》\"'.,"


def _unquote(target: str) -> str:
    """Decode URL-encoded markdown link targets (e.g. %20 -> space)."""
    if "%" not in target:
        return target
    try:
        return urllib.parse.unquote(target)
    except Exception:  # noqa: BLE001 - unquote is total in practice (pragma: no cover)
        return target


def _strip_anchor(target: str) -> str:
    return target.split("#", 1)[0]


def _is_pattern(path: str) -> bool:
    return any(ch in PATTERN_CHARS for ch in path)


def extract_link_targets(text: str) -> Iterable[str]:
    """Markdown link targets worth checking (external schemes and anchors skipped)."""
    for match in LINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target or target.startswith("#"):
            continue  # pure anchor
        if EXTERNAL_RE.match(target):
            continue  # http(s):, mailto:, ...
        target = _strip_anchor(target)
        if not target:
            continue
        yield _unquote(target)


def extract_backtick_paths(text: str) -> Iterable[str]:
    """Repo-rooted path tokens inside backtick spans.

    A span may hold a command (`` `python scripts/x.py check` ``); each
    whitespace-separated token is tested on its own so trailing arguments do
    not turn an existing path into a false finding.
    """
    for match in BACKTICK_RE.finditer(text):
        span = match.group(1).strip()
        if not span or EXTERNAL_RE.match(span):
            continue
        for token in span.split():
            token = token.strip(TRAILING_PUNCT)
            if not token or not token.startswith(REPO_PREFIXES):
                continue
            if _is_pattern(token):
                continue
            yield _strip_anchor(token)


def check_path(base_dir: Path, target: str) -> bool:
    """A pointer is intact when it resolves (relative to its file) to file or dir."""
    candidate = (base_dir / target).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except ValueError:
        return False  # escaped the repo (e.g. ../../.. beyond root)
    return candidate.exists()


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (line_no, kind, target) for every broken pointer in one file."""
    findings: list[tuple[int, str, str]] = []
    base_dir = path.parent
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        for target in extract_link_targets(line):
            if not check_path(base_dir, target):
                findings.append((line_no, "link", target))
        for target in extract_backtick_paths(line):
            repo_target = target.lstrip("/")
            if not check_path(REPO_ROOT, repo_target):
                findings.append((line_no, "backtick", target))
    return findings


def collect_targets() -> list[Path]:
    files: list[Path] = []

    def add(p: Path) -> None:
        if p.is_file():
            files.append(p)

    for p in sorted(REPO_ROOT.glob("spec/**/*")):
        if p.is_file() and p.suffix.lower() in SPEC_TEXT_SUFFIXES:
            files.append(p)
    add(REPO_ROOT / "CLAUDE.md")
    add(REPO_ROOT / "AGENTS.md")
    add(REPO_ROOT / "README.md")
    add(REPO_ROOT / "CONTRIBUTING.md")
    add(REPO_ROOT / "docs" / "README.md")
    for p in sorted((REPO_ROOT / "docs" / "dev_skills").glob("*/SKILL.md")):
        add(p)
    return sorted(set(files))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fix-report",
        action="store_true",
        help="print a repair suggestion per finding (never edits files)",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):  # Windows console mojibake guard
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    broken_total = 0
    for path in collect_targets():
        for line_no, kind, target in scan_file(path):
            rel = path.relative_to(REPO_ROOT).as_posix()
            print(f"{rel}:{line_no}: broken {kind} -> {target}")
            if args.fix_report:
                print("  fix: point to an existing file/dir (or drop the line and")
                print("       record the source in spec/collaboration/99_经验来源.md)")
            broken_total += 1

    if broken_total:
        print(f"pointer integrity: {broken_total} broken pointer(s)")
        return 1
    print("pointer integrity: all pointers resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())

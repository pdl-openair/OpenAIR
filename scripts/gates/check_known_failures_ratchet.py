#!/usr/bin/env python3
"""Known-failures list ratchet (M3): nodes[] may only shrink.

The D-15 ratchet in required-checks.yml judges unit-test RED/GREEN against
``.github/unit-test-known-failures.json`` - but nothing stopped a
contributor from APPENDING nodes to that file to silence brand-new
failures. The list itself had no machine guard.

This gate diffs the node ``id`` set of the working-tree file against the
same file at ``--base``: added ids are a violation unless ``--allow-growth``
is passed. CI NEVER passes that flag - growing the list requires editing
the workflow call line, which is a visible review surface. Removed ids
produce a non-blocking notice (shrink is the expected direction).

Bootstrap semantics mirror mypy_gate's H3 fix: an INVALID base ref is a
hard env error (exit 2, never silently green); a VALID base ref where the
file simply does not exist yet is a legitimate bootstrap (notice + pass).

Exit codes: 0 = pass (or bootstrap), 1 = violation (grown nodes[]),
2 = usage/config/env error (git failure, unparsable JSON on either side).

Pure Python >= 3.10, standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

DEFAULT_BASE = "origin/test"
DEFAULT_FILE = ".github/unit-test-known-failures.json"


def load_node_ids(text: str, origin: str) -> set[str]:
    """Parse a known-failures document -> set of node ids.

    ``origin`` labels error messages ("base"/"working tree"). Raises
    ValueError on anything that is not {"nodes": [{"id": str}, ...]}.
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{origin} file is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), list):
        raise ValueError(f"{origin} file must be an object with a 'nodes' list")
    ids: set[str] = set()
    for index, entry in enumerate(doc["nodes"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError(f"{origin} nodes[{index}] lacks a non-empty string 'id'")
        ids.add(entry["id"])
    return ids


def _git(argv: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def ref_exists(ref: str, cwd: str) -> bool:
    proc = _git(["git", "rev-parse", "--verify", "--quiet", ref], cwd)
    return proc.returncode == 0


def git_show(ref: str, path: str, cwd: str) -> str | None:
    """File content at ref; None when the file does not exist there."""
    proc = _git(["git", "show", f"{ref}:{path}"], cwd)
    if proc.returncode != 0:
        return None
    return proc.stdout


def default_root() -> str:
    """Repo root default: this script lives at <root>/scripts/gates/."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Known-failures ratchet: nodes[] may only shrink vs --base (M3)."
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="git ref holding the baseline file")
    parser.add_argument(
        "--file", default=DEFAULT_FILE, help="known-failures path (repo-relative)"
    )
    parser.add_argument("--root", default=None, help="repo root (default: parent of script dir)")
    parser.add_argument(
        "--allow-growth",
        action="store_true",
        help="permit added nodes with a notice (LOCAL ONLY - CI never passes this)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = os.path.abspath(args.root or default_root())

    # 当前文件先读先解析：无论基线是否存在，坏 JSON 都是配置错误（fail-closed）。
    # 文件整体删除 = 极致收缩（合法），按空集处理。
    current_path = os.path.join(root, *args.file.split("/"))
    try:
        with open(current_path, encoding="utf-8") as handle:
            current_text = handle.read()
    except FileNotFoundError:
        current_text = None
    except OSError as exc:
        print(f"[ratchet-file] cannot read {current_path}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if current_text is None:
        print(f"[ratchet-file] working-tree {args.file} absent - treated as maximal shrink")
        current_ids: set[str] = set()
    else:
        try:
            current_ids = load_node_ids(current_text, origin="working tree")
        except ValueError as exc:
            print(f"[ratchet-file] {exc}", file=sys.stderr)
            return EXIT_ERROR

    if not ref_exists(args.base, root):
        print(
            f"[ratchet-file] invalid base ref {args.base!r} (git rev-parse failed; expected "
            "e.g. origin/test or a commit sha) - refusing to judge, failing closed",
            file=sys.stderr,
        )
        return EXIT_ERROR

    base_text = git_show(args.base, args.file, root)
    if base_text is None:
        print(
            f"[ratchet-file] bootstrap: {args.file} absent at {args.base} - nothing to "
            "ratchet against, growth unchecked in this PR (file will become the baseline "
            "once merged)"
        )
        return EXIT_PASS

    try:
        base_ids = load_node_ids(base_text, origin="base")
    except ValueError as exc:
        print(f"[ratchet-file] {exc}", file=sys.stderr)
        return EXIT_ERROR

    added = sorted(current_ids - base_ids)
    removed = sorted(base_ids - current_ids)
    print(
        f"[ratchet-file] nodes[]: base {len(base_ids)} -> working tree {len(current_ids)} "
        f"(+{len(added)} / -{len(removed)}) vs {args.base}"
    )
    for node in removed:
        print(f"[ratchet-file]   - {node} (healed; shrink is the expected direction)")

    if added and not args.allow_growth:
        print(
            "[ratchet-file] BLOCKED: known-failures nodes[] grew - appending ids silences "
            "brand-new failures with no review. Fix or deselect the tests instead; if the "
            "growth is genuinely justified, the workflow call line in required-checks.yml "
            "must pass --allow-growth explicitly (visible review surface; CI never "
            "passes it silently).",
            file=sys.stderr,
        )
        for node in added:
            print(f"[ratchet-file]   + {node}", file=sys.stderr)
        return EXIT_VIOLATION
    if added and args.allow_growth:
        print(
            f"[ratchet-file] NOTICE: growth allowed by --allow-growth (local override); "
            f"{len(added)} node(s) added:"
        )
        for node in added:
            print(f"[ratchet-file]   + {node}")
    print("[ratchet-file] PASS: nodes[] did not grow")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

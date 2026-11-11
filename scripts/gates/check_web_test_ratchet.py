#!/usr/bin/env python3
"""Web test known-failures ratchet.

Vitest emits a JSON report (``--reporter=json``). CI runs the full web test
suite with ``continue-on-error`` so the report exists even when tests fail,
then this gate compares the actually-failing test ids against
``.github/web-test-known-failures.json``:

* a failing test absent from the list -> exit 1 (new failure, RED)
* a listed test that now passes -> non-blocking notice (shrink is expected)
* unreadable / empty report -> exit 2 (fail closed, never green by omission)

Ids are ``<repo-relative test file>::<full test name>`` so the list is stable
across local absolute paths and CI checkouts.

Usage:
  python scripts/gates/check_web_test_ratchet.py --report vitest-report.json
  python scripts/gates/check_web_test_ratchet.py --report r.json \
      --known-failures .github/web-test-known-failures.json

Pure Python >= 3.10, standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_ERROR = 2

DEFAULT_KNOWN = ".github/web-test-known-failures.json"


def _relative(path: str, root: Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix()


def load_report(path: Path, root: Path) -> dict[str, str]:
    """Return {id: status} for every assertion in a Vitest JSON report."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Vitest report {path}: {exc}") from exc
    results = payload.get("testResults")
    if not isinstance(results, list) or not results:
        raise ValueError(f"Vitest report {path} has no testResults")
    nodes: dict[str, str] = {}
    for file_result in results:
        if not isinstance(file_result, dict):
            continue
        file_name = _relative(str(file_result.get("name", "")), root)
        assertions = file_result.get("assertionResults")
        if not isinstance(assertions, list):
            continue
        for assertion in assertions:
            if not isinstance(assertion, dict):
                continue
            full_name = str(assertion.get("fullName", "")).strip()
            status = str(assertion.get("status", "")).strip()
            if file_name and full_name and status:
                nodes[f"{file_name}::{full_name}"] = status
    if not nodes:
        raise ValueError(f"Vitest report {path} contains no assertions")
    return nodes


def load_known(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read known-failures file {path}: {exc}") from exc
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError(f"known-failures file {path} must contain a nodes list")
    ids: set[str] = set()
    for index, entry in enumerate(nodes):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError(f"known-failures nodes[{index}] lacks a non-empty string id")
        ids.add(entry["id"])
    return ids


def evaluate(report: dict[str, str], known: set[str]) -> tuple[set[str], set[str], set[str]]:
    """Return (new_failures, fixed_known, still_failing_known)."""
    failing = {node for node, status in report.items() if status == "failed"}
    new_failures = failing - known
    still_failing_known = failing & known
    fixed_known = known - failing
    return new_failures, fixed_known, still_failing_known


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", required=True, help="Vitest JSON report path")
    parser.add_argument("--known-failures", default=DEFAULT_KNOWN)
    parser.add_argument("--root", default=None, help="repository root (default: cwd)")
    args = parser.parse_args(argv)

    root = Path(args.root or os.getcwd())
    try:
        report = load_report(Path(args.report), root)
        known = load_known(Path(args.known_failures))
    except ValueError as exc:
        print(f"[web-test-ratchet] {exc}", file=sys.stderr)
        return EXIT_ERROR

    new_failures, fixed_known, still_failing = evaluate(report, known)
    for node in sorted(still_failing):
        print(f"[web-test-ratchet] known failure: {node}")
    for node in sorted(fixed_known):
        print(f"[web-test-ratchet] known failure now passes (shrink the list): {node}")
    for node in sorted(new_failures):
        print(f"[web-test-ratchet] NEW FAILURE: {node}", file=sys.stderr)

    print(
        f"[web-test-ratchet] {len(new_failures)} new, "
        f"{len(still_failing)} known failing, {len(fixed_known)} known fixed"
    )
    return EXIT_VIOLATION if new_failures else EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())

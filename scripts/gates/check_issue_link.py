#!/usr/bin/env python3
"""Verify that fix-class PRs touching product code reference a verified issue.

Split out of the pr-evidence job's inline python block (AI4SE harness
refactor, sync batch M2 / design §3.4; contract SPEC-PROC-02).

Policy (unchanged from the inline block it replaces):
  * PR carries the ``no-issue-required`` label -> skip (escape hatch).
  * PR title is not ``fix``-class -> skip.
  * PR touches no product code (``services/``, ``apps/``, ``packages/``
    minus test files) -> skip.
  * Otherwise the body must reference at least one issue (``#123``) and at
    least one referenced issue must carry the ``status/verified`` label,
    else BLOCKED (non-zero exit).

Consumes PR_TITLE / PR_BODY / PR_NUMBER / GITHUB_REPOSITORY from the
environment (--from-env), mirroring validate_pr_evidence.py's convention.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


def gh(*args: str) -> str:
    proc = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"[issue-link] gh api failed: {proc.stderr.strip()}")
    return proc.stdout


def is_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in path
        or base.startswith("test_")
        or base.endswith("_test.py")
        or ".test." in base
        or ".spec." in base
    )


def check_from_env() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    number = os.environ["PR_NUMBER"]

    labels = {
        line
        for line in gh(f"repos/{repo}/pulls/{number}", "--jq=.labels[].name").splitlines()
        if line
    }
    if "no-issue-required" in labels:
        print("[issue-link] escape label 'no-issue-required' present; skip")
        return 0

    if not re.match(r"^\s*fix(\([^)]*\))?:", os.environ.get("PR_TITLE", "")):
        print("[issue-link] PR title is not fix-class; skip")
        return 0

    files = [
        line
        for line in gh(
            "--paginate",
            f"repos/{repo}/pulls/{number}/files",
            "--jq=.[].filename",
        ).splitlines()
        if line
    ]

    product = [
        path
        for path in files
        if path.startswith(("services/", "apps/", "packages/"))
        and not is_test_file(path)
    ]
    if not product:
        print("[issue-link] fix-class PR touches no product code; skip")
        return 0

    refs = re.findall(r"#(\d+)", os.environ.get("PR_BODY", "") or "")
    if not refs:
        sys.exit(
            "[issue-link] BLOCKED: fix-class PR touching product code must "
            "reference an issue (e.g. 'Closes #123') [SPEC-PROC-02]"
        )
    refs = list(dict.fromkeys(refs))
    for ref in refs:
        issue_labels = {
            line
            for line in gh(f"repos/{repo}/issues/{ref}", "--jq=.labels[].name").splitlines()
            if line
        }
        if "status/verified" in issue_labels:
            print(f"[issue-link] issue #{ref} carries status/verified; PASS")
            return 0
    sys.exit(
        "[issue-link] BLOCKED: none of the referenced issues ["
        + ", ".join(f"#{ref}" for ref in refs)
        + "] carry the status/verified label [SPEC-PROC-02]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from-env",
        action="store_true",
        required=True,
        help="read PR_TITLE / PR_BODY / PR_NUMBER / GITHUB_REPOSITORY from the environment",
    )
    parser.parse_args(argv)
    return check_from_env()


if __name__ == "__main__":
    sys.exit(main())

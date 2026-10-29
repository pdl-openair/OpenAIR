#!/usr/bin/env python3
"""Read-only validation for a workflow pack and its selected Skill composition."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from skill_registry.workflow_validation import validate_workflow_pack  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a workflow pack without changing files.")
    parser.add_argument("--pack", required=True, help="Pack key declared in schemas/packs.yaml.")
    parser.add_argument(
        "--composition-map",
        help="Repository-relative composition map when the workflow does not use the default substage-skill-map.yaml.",
    )
    parser.add_argument(
        "--strict-runtime",
        action="store_true",
        help="Treat missing runtime contracts on legacy claude_code Skills as errors.",
    )
    args = parser.parse_args(argv)
    root = SCRIPTS.parent
    composition_map = Path(args.composition_map) if args.composition_map else None
    issues = validate_workflow_pack(root, pack=args.pack, composition_map=composition_map)
    if not issues:
        print(f"workflow integration check passed: pack={args.pack}")
        return 0
    errors = [issue for issue in issues if issue.severity == "error"]
    if args.strict_runtime:
        errors.extend(issue for issue in issues if issue.code == "RUNTIME_CONTRACT_MISSING")
    for issue in issues:
        print(f"[{issue.severity.upper()}] {issue.code}: {issue.message}")
    if errors:
        print(f"workflow integration check failed: {len(errors)} error(s)")
        return 1
    print(f"workflow integration check passed with {len(issues)} warning(s): pack={args.pack}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

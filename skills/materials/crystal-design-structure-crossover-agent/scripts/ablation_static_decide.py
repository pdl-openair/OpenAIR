#!/usr/bin/env python3
"""Apply STATIC_RULES steering between generations.

Reads snapshot_latest.json, calls static_rules_decide, and either:
  default:   invokes evolve_driver.py steer via subprocess so the same
             mutation + steer_log.jsonl append path is exercised.
  --dry-run: prints the chosen SteerAction JSON to stdout (no mutation).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from steering_modes import static_rules_decide  # noqa: E402


def _build_steer_cli_args(action) -> list[str]:
    """Translate SteerAction into the equivalent `evolve_driver steer` argv."""
    if action.action == "ban":
        return ["--ban", str(action.args.get("element", ""))]
    if action.action == "noise":
        return ["--noise-level", str(action.args.get("level", 0.3))]
    if action.action == "constraint":
        # `evolve_driver.py steer --weight KEY:WEIGHT` adjusts an EXISTING
        # constraint's weight (cmd_steer checks `if key in constraints`).
        # static_rules_decide's Rule 4 fires only when
        # most_violated_constraint is set, which implies the constraint already
        # exists in constraints.json. We add weight_delta to current weight;
        # if the key is somehow missing, cmd_steer silently no-ops on it.
        key = action.args.get("key", "")
        weight_delta = int(action.args.get("weight_delta", 0))
        return ["--weight", f"{key}:{weight_delta}"]
    return []  # noop or unknown


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--family", default="default")
    p.add_argument("--driver", default=str(_HERE / "evolve_driver.py"),
                   help="Path to evolve_driver.py (for --apply)")
    args = p.parse_args()

    snapshot_path = args.run_dir / "snapshot_latest.json"
    if not snapshot_path.exists():
        print(json.dumps({"action": "noop", "args": {}, "reason": "no snapshot yet"}))
        return 0

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    constraints = {}
    cpath = args.run_dir / "constraints.json"
    if cpath.exists():
        constraints = json.loads(cpath.read_text(encoding="utf-8"))

    action = static_rules_decide(snapshot=snapshot, family=args.family, constraints=constraints)
    payload = {"action": action.action, "args": action.args, "reason": action.reason}
    print(json.dumps(payload, ensure_ascii=False))

    if not args.dry_run and action.action != "noop":
        cli = [sys.executable, args.driver, "steer", "--run-dir", str(args.run_dir)]
        cli.extend(_build_steer_cli_args(action))
        result = subprocess.run(cli, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[ablation_static_decide] steer failed: {result.stderr}", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())

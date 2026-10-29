#!/usr/bin/env python3
"""Apply G-rule bandgap weight scheduling between generations (steering_parity2 实验二).

Reads snapshot_latest.json + constraints.json, calls
steering_modes.bandgap_rule_decide, and either:
  default:   invokes evolve_driver.py steer via subprocess (absolute weight via
             `steer --constraint bandgap:MIN:MAX:W`) so the same steer_log.jsonl
             append path is exercised.
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

from steering_modes import bandgap_rule_decide  # noqa: E402


def _build_steer_cli_args(action) -> list[str]:
    """Translate the SteerAction into `evolve_driver steer` argv.

    weight 语义是绝对值(cmd_steer 直接覆盖 constraints[key]["weight"]),
    与预注册规则的 ±5 步进结果一致:规则侧算好绝对权重,这里原样下发。
    """
    if action.action == "constraint":
        key = action.args.get("key", "bandgap")
        mn = action.args.get("min")
        mx = action.args.get("max")
        w = action.args.get("weight")
        return ["--constraint", f"{key}:{'' if mn is None else mn}:{'' if mx is None else mx}:{w}"]
    return []  # noop or unknown


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--driver", default=str(_HERE / "evolve_driver.py"))
    args = p.parse_args()

    snapshot_path = args.run_dir / "snapshot_latest.json"
    if not snapshot_path.exists():
        print(json.dumps({"action": "noop", "args": {}, "reason": "no snapshot yet"}))
        return 0
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cpath = args.run_dir / "constraints.json"
    constraints = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}

    action = bandgap_rule_decide(snapshot=snapshot, constraints=constraints)
    payload = {"action": action.action, "args": action.args, "reason": action.reason}
    print(json.dumps(payload, ensure_ascii=False))

    if not args.dry_run and action.action != "noop":
        cli = [sys.executable, args.driver, "steer", "--run-dir", str(args.run_dir)]
        cli.extend(_build_steer_cli_args(action))
        result = subprocess.run(cli, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[bandgap_rule_decide] steer failed: {result.stderr}", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Apply noise-rule scheduling between generations (steering_parity3, 单杠杆对照臂).

每代 step 且 phase_switcher 之后调用:读 snapshot_latest.json(当前)+
此前各代快照(SUN/inband 平台检测)+ config.json(当前 noise)→
steering_modes.noise_rule_decide → `steer --noise-level`(绝对值)。
用法: python noise_rule_step.py --run-dir <RUN_DIR> [--dry-run]
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

from steering_modes import noise_rule_decide  # noqa: E402


def prior_stat_pairs(run_dir: Path, cur_gen: int) -> list:
    """之前各代的 (sun_count, bandgap inband 率) 二元组,旧→新。"""
    out = []
    for g in range(max(cur_gen - 4, 1), cur_gen):  # 平台窗口只需 2 代,取 4 代冗余
        p = run_dir / f"snapshot_gen{g}.json"
        if not p.exists():
            continue
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
            stats = (snap.get("constraint_key_stats") or {}).get("bandgap") or {}
            out.append((snap.get("sun_count"),
                        round(float(stats.get("inband_rate", 0.0)), 4)))
        except Exception:
            continue
    return out


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
    cfg_path = args.run_dir / "config.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    prior = prior_stat_pairs(args.run_dir, int(snapshot.get("generation", 0)))

    action = noise_rule_decide(snapshot=snapshot, config=config, prior_stats=prior)
    payload = {"action": action.action, "args": action.args, "reason": action.reason}
    print(json.dumps(payload, ensure_ascii=False))

    if not args.dry_run and action.action == "noise":
        cli = [sys.executable, args.driver, "steer", "--run-dir", str(args.run_dir),
               "--noise-level", str(action.args["level"])]
        result = subprocess.run(cli, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[noise_rule_step] steer failed: {result.stderr}", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())

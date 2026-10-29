#!/usr/bin/env python3
"""steering_parity3 车道相位切换器(环境事件,所有臂完全一致,非调度决策)。

每代 step 后由车道脚本调用:读 snapshot_latest.json + phase_state.json,
Phase A 且 (bandgap inband_rate ≥ 0.60 或 gen ≥ 8 硬上限) →
`steer --constraint bandgap:2.7:3.3:10`(切换同时复位任何边界微调;
w 恒 10)。切换记录落 phase_switch.jsonl(触发代数/是否硬切/当时带内率),
判级的「切换代数」次级指标从这里聚合。

用法: python phase_switcher.py --run-dir <RUN_DIR> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from steering_modes import (  # noqa: E402
    PARITY3_BAND_WEIGHT,
    PHASE_B_BAND,
    phase_switch_decide,
)

STATE_FILE = "phase_state.json"
LOG_FILE = "phase_switch.jsonl"


def load_state(run_dir: Path) -> dict:
    p = run_dir / STATE_FILE
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"phase": "A", "switch_gen": None}


def save_state(run_dir: Path, state: dict) -> None:
    (run_dir / STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_log(run_dir: Path, entry: dict) -> None:
    with (run_dir / LOG_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--driver", default=str(_HERE / "evolve_driver.py"))
    args = p.parse_args()

    snapshot_path = args.run_dir / "snapshot_latest.json"
    if not snapshot_path.exists():
        print(json.dumps({"switch": False, "reason": "no snapshot yet"}))
        return 0
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    state = load_state(args.run_dir)

    decision = phase_switch_decide(snapshot, state)
    decision["phase"] = state.get("phase")
    print(json.dumps(decision, ensure_ascii=False))

    if not decision.get("switch") or args.dry_run:
        return 0

    cli = [sys.executable, args.driver, "steer", "--run-dir", str(args.run_dir),
           "--constraint",
           f"bandgap:{PHASE_B_BAND[0]}:{PHASE_B_BAND[1]}:{PARITY3_BAND_WEIGHT:g}"]
    result = subprocess.run(cli, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[phase_switcher] steer failed: {result.stderr}", file=sys.stderr)
        return result.returncode

    new_state = {"phase": "B", "switch_gen": decision["gen"],
                 "switched_at": time.strftime("%F %T")}
    save_state(args.run_dir, new_state)
    append_log(args.run_dir, {
        "gen": decision["gen"],
        "inband_rate": decision.get("inband_rate"),
        "forced": decision.get("forced", False),
        "reason": decision.get("reason"),
        "at": new_state["switched_at"],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())

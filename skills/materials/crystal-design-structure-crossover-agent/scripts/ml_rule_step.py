#!/usr/bin/env python3
"""Apply ML-rule multi-lever scheduling between generations (steering_parity3).

每代 step 且 phase_switcher 之后调用。多杠杆阈值规则(预注册 §4,优先级
①边界 ②降噪 ③升噪 ④ban),无独立状态文件 —— 放宽量从 constraints 当前带
vs 名义带推出,ban 次数从 element_set.banned 推出,断电重跑不丢账。
用法: python ml_rule_step.py --run-dir <RUN_DIR> [--dry-run]
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

from noise_rule_step import prior_stat_pairs  # noqa: E402
from steering_modes import ml_rule_decide  # noqa: E402


def load_phase_state(run_dir: Path) -> dict:
    p = run_dir / "phase_state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"phase": "A", "switch_gen": None}


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
    run_dir: Path = args.run_dir
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cfg_path = run_dir / "config.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cpath = run_dir / "constraints.json"
    constraints = json.loads(cpath.read_text(encoding="utf-8")) if cpath.exists() else {}
    prior = prior_stat_pairs(run_dir, int(snapshot.get("generation", 0)))
    phase_state = load_phase_state(run_dir)

    action = ml_rule_decide(snapshot=snapshot, constraints=constraints, config=config,
                            prior_stats=prior, phase_state=phase_state)
    payload = {"action": action.action, "args": action.args, "reason": action.reason}
    print(json.dumps(payload, ensure_ascii=False))

    if args.dry_run or action.action == "noop":
        return 0
    cli = [sys.executable, args.driver, "steer", "--run-dir", str(run_dir)]
    if action.action == "constraint":
        a = action.args
        cli += ["--constraint",
                f"{a.get('key', 'bandgap')}:{a.get('min')}:{a.get('max')}:{a.get('weight', 10)}"]
    elif action.action == "noise":
        cli += ["--noise-level", str(action.args["level"])]
    elif action.action == "ban":
        cli += ["--ban", str(action.args["element"])]
    else:
        return 0
    result = subprocess.run(cli, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ml_rule_step] steer failed: {result.stderr}", file=sys.stderr)
        return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())

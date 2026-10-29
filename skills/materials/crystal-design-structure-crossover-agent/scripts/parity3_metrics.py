#!/usr/bin/env python3
"""steering_parity3:Phase-B 带内 mSUN 密度聚合 + 非平稳次级指标与判级。

主指标零改动复用 bandgap_parity2_metrics(判级恒按名义 Phase B 带
[2.7,3.3],与臂内边界微调无关)。新增次级指标(预注册 §5):
  - 切换代数与硬切标记(phase_switch.jsonl)
  - 按杠杆的 steer 使用统计:noise / boundary / chem(ban|allow_only),
    环境切换条目(constraint=名义带且与 phase_switch 数对齐)单列不计入杠杆
  - 切换后前 3 代恢复曲线(inband / valid)
  - conc / uniqueness 轨迹
  - memory 命中(ML-agent 披露项)

判级(预注册 §5):
  强阳性:ML-agent 密度 CI 下界 > max(static, noise_rule, ml_rule) 点估计
  弱阳性:ML-agent > ml_rule(p<0.05)且 > noise_rule(p<0.05)
  阴性:  ML-agent ≈ ml_rule(p>0.05)

用法:
  python3 parity3_metrics.py \
    --arm static='.../steering_parity3/static_rep*' \
    --arm noise_rule='.../steering_parity3/noise_rule_rep*' \
    --arm ml_rule='.../steering_parity3/ml_rule_rep*' \
    --arm ml_agent='.../steering_parity3/ml_agent_rep*' \
    --out steering_parity3_metrics.json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import fisher_exact

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from bandgap_parity2_metrics import (  # noqa: E402
    collect_arm,
    inband_msun_flags,
    prop_ci,
)
from parity_metrics import amd_matrix, fmd_inv, uniqueness_flags  # noqa: E402
from steering_modes import PHASE_B_BAND  # noqa: E402

AGENT_ARM = "ml_agent"
RULE_ARM = "ml_rule"
NOISE_RULE_ARM = "noise_rule"
CONTROL_ARMS = ("static", "noise_rule", "ml_rule")
RECOVERY_GENS = 3

_SWITCH_RULE = f"bandgap:{PHASE_B_BAND[0]}:{PHASE_B_BAND[1]}:10"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def lever_usage(run_dir: Path) -> dict:
    """按杠杆分类 steer 使用次数(归因审计)。

    环境切换 = constraint 条目且 rules==名义带;此类条目数量以
    phase_switch.jsonl 的实际切换次数为上限对齐(防止把规则/agent 收回到
    名义带的动作误计为环境)。超出上限的名义带条目计回 boundary。
    """
    steers = _read_jsonl(run_dir / "steer_log.jsonl")
    switches = _read_jsonl(run_dir / "phase_switch.jsonl")
    n_switch = len(switches)
    usage = {"noise": 0, "noise_levels": [], "boundary": 0, "chem": 0,
             "chem_details": [], "environment_switch": 0, "noop": 0}
    nominal_seen = 0
    for e in steers:
        act = e.get("action")
        if act == "noise":
            usage["noise"] += 1
            usage["noise_levels"].append((e.get("args") or {}).get("level"))
        elif act in ("ban", "allow_only", "clear_ban"):
            usage["chem"] += 1
            usage["chem_details"].append({"action": act, "args": e.get("args") or {}})
        elif act == "constraint":
            rules = (e.get("args") or {}).get("rules") or []
            if _SWITCH_RULE in rules and nominal_seen < n_switch:
                nominal_seen += 1
                usage["environment_switch"] += 1
            else:
                usage["boundary"] += 1
        elif act == "noop":
            usage["noop"] += 1
    return usage


def run_secondary(run_dir: Path) -> dict:
    """单 run 次级指标:切换代数 / 恢复曲线 / conc-uniq 轨迹 / memory 命中。"""
    out: dict = {"levers": lever_usage(run_dir)}
    switches = _read_jsonl(run_dir / "phase_switch.jsonl")
    out["switch_gen"] = switches[0]["gen"] if switches else None
    out["switch_forced"] = bool(switches[0].get("forced")) if switches else None

    recovery = []
    if switches:
        g0 = int(switches[0]["gen"])
        for g in range(g0 + 1, g0 + 1 + RECOVERY_GENS):
            p = run_dir / f"snapshot_gen{g}.json"
            if not p.exists():
                continue
            try:
                snap = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            stats = (snap.get("constraint_key_stats") or {}).get("bandgap") or {}
            recovery.append({
                "gen": g,
                "inband_rate": stats.get("inband_rate"),
                "valid_rate": (snap.get("n_valid", 0) / snap.get("pop_size", 1)
                               if snap.get("pop_size") else None),
            })
    out["recovery_curve"] = recovery

    traj = []
    for p in sorted(run_dir.glob("snapshot_gen*.json")):
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        traj.append({"gen": snap.get("generation"),
                     "family_concentration": snap.get("family_concentration"),
                     "uniqueness_rate": snap.get("uniqueness_rate"),
                     "sun_count": snap.get("sun_count")})
    out["trajectory"] = traj

    # memory_query_result.json 每代覆盖,只存终代快照的检索结果
    # (逐代命中由 ML-agent 的 decisions.md 披露,此处为终态口径)
    q = run_dir / "memory_query_result.json"
    q_size = None
    if q.exists():
        try:
            q_size = len(json.loads(q.read_text(encoding="utf-8")))
        except Exception:
            q_size = None
    out["memory_final_query_hits"] = q_size
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arm", action="append", required=True, help="NAME=GLOB,可重复")
    p.add_argument("--boot", type=int, default=2000)
    p.add_argument("--amd-k", type=int, default=100)
    p.add_argument("--reference-arm", default="static", help="FMD⁻¹ 的参考分布臂")
    p.add_argument("--seed", type=int, default=20260919)
    p.add_argument("--out", default="steering_parity3_metrics.json")
    args = p.parse_args()

    rng = np.random.default_rng(args.seed)
    arms: dict[str, list[dict]] = {}
    arm_dirs: dict[str, list[str]] = {}
    for spec in args.arm:
        name, _, pattern = spec.partition("=")
        if not pattern:
            sys.exit(f"--arm 格式应为 NAME=GLOB: {spec!r}")
        arms[name] = collect_arm(pattern)
        arm_dirs[name] = sorted(d for d in glob.glob(pattern) if Path(d).is_dir())
        print(f"==> 臂 {name}: 池化 {len(arms[name])} 个终态结构({len(arm_dirs[name])} runs)")
    if args.reference_arm not in arms:
        sys.exit(f"--reference-arm {args.reference_arm!r} 不在 --arm 列表里")

    aggregate = {}
    for name, records in arms.items():
        uniq = uniqueness_flags(records)
        flags = inband_msun_flags(records, uniq)
        point, lo, hi = prop_ci(flags, args.boot, rng)
        aggregate[name] = {
            "n_total": len(records),
            "inband_msun_density": {"point": point, "ci_lo": lo, "ci_hi": hi},
            "n_inband_msun": int(flags.sum()),
            "n_unique": int(uniq.sum()),
            "per_run_secondary": [run_secondary(Path(d)) for d in arm_dirs[name]],
        }
        a = aggregate[name]
        print(f"  {name}: 带内mSUN密度={point:.3f} [{lo:.3f},{hi:.3f}] "
              f"({a['n_inband_msun']}/{a['n_total']})")

    ref_emb = amd_matrix(arms[args.reference_arm], args.amd_k)
    for name, records in arms.items():
        emb = ref_emb if name == args.reference_arm else amd_matrix(records, args.amd_k)
        aggregate[name]["fmd_inv"] = fmd_inv(emb, ref_emb)
        print(f"  {name}: FMD⁻¹={aggregate[name]['fmd_inv']:.4f} (vs {args.reference_arm})")

    pairwise = {}
    names = list(arms)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = aggregate[names[i]], aggregate[names[j]]
            table = [[a["n_inband_msun"], a["n_total"] - a["n_inband_msun"]],
                     [b["n_inband_msun"], b["n_total"] - b["n_inband_msun"]]]
            _, pval = fisher_exact(table, alternative="two-sided")
            pairwise[f"{names[i]}_vs_{names[j]}"] = {
                "density_a": a["inband_msun_density"]["point"],
                "density_b": b["inband_msun_density"]["point"],
                "p_value": float(pval),
            }

    def _pair(x: str, y: str):
        return pairwise.get(f"{x}_vs_{y}") or pairwise.get(f"{y}_vs_{x}")

    verdict: dict = {"available": AGENT_ARM in arms}
    if verdict["available"]:
        ag = aggregate[AGENT_ARM]["inband_msun_density"]
        controls = {n: aggregate[n]["inband_msun_density"]["point"]
                    for n in CONTROL_ARMS if n in aggregate}
        strong = bool(controls) and ag["ci_lo"] > max(controls.values())
        pr_rule = _pair(AGENT_ARM, RULE_ARM)
        pr_noise = _pair(AGENT_ARM, NOISE_RULE_ARM)
        agent_rule_p = pr_rule["p_value"] if pr_rule else None
        agent_noise_p = pr_noise["p_value"] if pr_noise else None
        beats_rule = bool(pr_rule and agent_rule_p < 0.05
                          and pr_rule["density_a"] > pr_rule["density_b"])
        beats_noise = bool(pr_noise and agent_noise_p < 0.05
                           and pr_noise["density_a"] > pr_noise["density_b"])
        ties_rule = bool(pr_rule and agent_rule_p > 0.05)
        if strong:
            verdict["grade"] = "strong_positive"
        elif beats_rule and beats_noise:
            verdict["grade"] = "weak_positive"
        elif ties_rule:
            verdict["grade"] = "negative"
        else:
            verdict["grade"] = "inconclusive"
        verdict.update({
            "agent_ci_lo": ag["ci_lo"],
            "control_points": controls,
            "agent_vs_rule_p": agent_rule_p,
            "agent_vs_noise_rule_p": agent_noise_p,
        })
        print(f"==> 判级: {verdict['grade']} "
              f"(vs ml_rule p={agent_rule_p}, vs noise_rule p={agent_noise_p})")

    result = {
        "scheme": "steering_parity3_dualphase",
        "boot": args.boot,
        "aggregate": aggregate,
        "pairwise": pairwise,
        "verdict": verdict,
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"==> 写出 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""steering_parity2 实验二:带内 mSUN 密度聚合与判级(docs/exp-plan/steering_parity2.md §B4)。

主指标:带内 mSUN 密度 = #(relaxed 后 ehull<0.1 ∧ novel ∧ unique ∧ bandgap∈[2.7,3.3])
        / 池化终态总数(分母含评估失败个体,与方案口径一致;完整个案率另报)。
unique 维 = 臂内池化 StructureMatcher 先到先得(复用 parity_metrics.uniqueness_flags)。
输入:各 run 目录的 per_structure_final_metrics.json(bandgap_final_eval.py 产出)。

判级(预注册):
  强阳性:G-agent 密度 CI 下界 > max(G-no, G-fix, G-rule) 各自点估计
  弱阳性:G-agent > G-rule(Fisher p<0.05)
  阴性:  G-agent ≈ G-rule(p>0.05)

用法:
  python3 bandgap_parity2_metrics.py \
    --arm g_no='.../steering_parity2/g_no_rep*' \
    --arm g_fix='.../steering_parity2/g_fix_rep*' \
    --arm g_rule='.../steering_parity2/g_rule_rep*' \
    --arm g_agent='.../steering_parity2/g_agent_rep*' \
    --out steering_parity2_bandgap_metrics.json
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

from parity_metrics import amd_matrix, fmd_inv, uniqueness_flags  # noqa: E402

INBAND_LO, INBAND_HI = 2.7, 3.3  # 判级带宽(与 bandgap_final_eval.py 一致)
METASTABLE_EHULL = 0.1

AGENT_ARM = "g_agent"
RULE_ARM = "g_rule"
CONTROL_ARMS = ("g_no", "g_fix", "g_rule")


def collect_arm(pattern: str) -> list[dict]:
    """glob 匹配 run 目录,池化各 per_structure_final_metrics.json 的 structures。"""
    dirs = sorted(d for d in glob.glob(pattern) if Path(d).is_dir())
    if not dirs:
        sys.exit(f"臂目录 glob 无匹配: {pattern}")
    records: list[dict] = []
    for d in dirs:
        f = Path(d) / "per_structure_final_metrics.json"
        if not f.exists():
            sys.exit(f"{f} 缺失:先对 {d} 跑 bandgap_final_eval.py")
        payload = json.loads(f.read_text(encoding="utf-8"))
        records.extend(payload.get("structures") or [])
    return records


def _flag(v) -> float:
    """评估失败(None)→ 0:计入分母、不计入分子(方案密度口径,保守)。"""
    return 0.0 if v is None else float(v)


def inband_msun_flags(records: list[dict], uniq: np.ndarray) -> np.ndarray:
    return np.array([
        _flag(r.get("metastable")) * _flag(r.get("novel"))
        * uniq[i] * _flag(r.get("inband"))
        for i, r in enumerate(records)
    ])


def prop_ci(flags: np.ndarray, boot: int, rng: np.random.Generator):
    if len(flags) == 0:
        return float("nan"), float("nan"), float("nan")
    point = float(flags.mean())
    n = len(flags)
    draws = [float(rng.choice(flags, size=n, replace=True).mean()) for _ in range(boot)]
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, float(lo), float(hi)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arm", action="append", required=True, help="NAME=GLOB,可重复")
    p.add_argument("--boot", type=int, default=2000)
    p.add_argument("--amd-k", type=int, default=100)
    p.add_argument("--reference-arm", default="g_no", help="FMD⁻¹ 的参考分布臂")
    p.add_argument("--seed", type=int, default=20260913)
    p.add_argument("--out", default="steering_parity2_bandgap_metrics.json")
    args = p.parse_args()

    rng = np.random.default_rng(args.seed)
    arms: dict[str, list[dict]] = {}
    for spec in args.arm:
        name, _, pattern = spec.partition("=")
        if not pattern:
            sys.exit(f"--arm 格式应为 NAME=GLOB: {spec!r}")
        arms[name] = collect_arm(pattern)
        print(f"==> 臂 {name}: 池化 {len(arms[name])} 个终态结构")
    if args.reference_arm not in arms:
        sys.exit(f"--reference-arm {args.reference_arm!r} 不在 --arm 列表里")

    aggregate = {}
    for name, records in arms.items():
        uniq = uniqueness_flags(records)
        flags = inband_msun_flags(records, uniq)
        point, lo, hi = prop_ci(flags, args.boot, rng)
        complete = [i for i, r in enumerate(records)
                    if None not in (r.get("metastable"), r.get("novel"), r.get("inband"))]
        complete_rate = (float(flags[complete].mean()) if complete else float("nan"))
        bgs = [r.get("bandgap_alignn") for r in records
               if isinstance(r.get("bandgap_alignn"), (int, float))]
        aggregate[name] = {
            "n_total": len(records),
            "inband_msun_density": {"point": point, "ci_lo": lo, "ci_hi": hi},
            "n_inband_msun": int(flags.sum()),
            "complete_case_rate": complete_rate,
            "n_complete_case": len(complete),
            "n_unique": int(uniq.sum()),
            "bandgap_values": bgs,
            "bandgap_inband_rate": (
                float(np.mean([1.0 if INBAND_LO <= b <= INBAND_HI else 0.0 for b in bgs]))
                if bgs else float("nan")),
        }
        a = aggregate[name]
        print(f"  {name}: 带内mSUN密度={point:.3f} [{lo:.3f},{hi:.3f}] "
              f"({a['n_inband_msun']}/{a['n_total']}; 完整个案 {complete_rate:.3f}) "
              f"带内率={a['bandgap_inband_rate']:.3f}")

    # FMD⁻¹(几何覆盖,必报观测;参考臂默认 g_no)
    ref_emb = amd_matrix(arms[args.reference_arm], args.amd_k)
    for name, records in arms.items():
        emb = ref_emb if name == args.reference_arm else amd_matrix(records, args.amd_k)
        aggregate[name]["fmd_inv"] = fmd_inv(emb, ref_emb)
        print(f"  {name}: FMD⁻¹={aggregate[name]['fmd_inv']:.4f} (vs {args.reference_arm})")

    # 两两 Fisher 精确检验(带内 mSUN 计数)
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

    # 预注册判级
    verdict: dict = {"available": AGENT_ARM in arms}
    if verdict["available"]:
        ag = aggregate[AGENT_ARM]["inband_msun_density"]
        controls = {n: aggregate[n]["inband_msun_density"]["point"]
                    for n in CONTROL_ARMS if n in aggregate}
        strong = bool(controls) and ag["ci_lo"] > max(controls.values())
        pr = pairwise.get(f"{AGENT_ARM}_vs_{RULE_ARM}") or pairwise.get(f"{RULE_ARM}_vs_{AGENT_ARM}")
        if pr:  # 归一化方向为 agent 在前
            pr = {"density_a": pr["density_b"], "density_b": pr["density_a"],
                  "p_value": pr["p_value"]}
        weak = bool(pr and pr["p_value"] < 0.05
                    and pr["density_a"] > pr["density_b"])
        negative = bool(pr and pr["p_value"] > 0.05)
        if strong:
            verdict["grade"] = "strong_positive"
        elif weak:
            verdict["grade"] = "weak_positive"
        elif negative:
            verdict["grade"] = "negative"
        else:
            verdict["grade"] = "inconclusive"
        verdict.update({
            "agent_ci_lo": ag["ci_lo"],
            "control_points": controls,
            "agent_vs_rule_p": pr["p_value"] if pr else None,
        })
        print(f"==> 判级: {verdict['grade']}")

    result = {
        "scheme": "steering_parity2_bandgap",
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

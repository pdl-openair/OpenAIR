# scripts/random_walk_runner.py
"""random_walk 臂驱动（evo_sun_parity 实验方案 §4）。

无选择的突变链：从种子池不重复抽 --chains 条链，每链连续 mutate_structure
--steps 轮（noise 与 EA 臂同参，无 crossover / 无选择 / 无淘汰），只保留终态。
终态用 mattergen_eval backend 表征（relax + batch-of-1 evaluate，与 EA 臂同
协议），落盘：

  <run-dir>/chain_<i>_final.cif          终态结构
  <run-dir>/per_structure_metrics.json   逐体指标（供 parity_metrics.py 读取）

用途：隔离「突变算子本身」的贡献——EA 臂 = 突变 + 选择(+steering)，本臂 = 纯突变。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_client import call_async_tool, get_endpoints  # noqa: E402
from evaluators import get_backend  # noqa: E402

# 终态表征需要的键（parity_metrics 的六指标从此取值）
EVAL_KEYS = ["ehull_eval", "ehull_sat", "novel", "stable", "comp_valid_eval", "rmsd_relax"]

METRIC_OUT_KEYS = [
    "name", "ehull_eval", "ehull_sat", "novel", "stable",
    "comp_valid_eval", "structure_valid", "rmsd_relax", "formula",
]


def run_chain(
    eps: dict, seed_cif: str, chain_id: str, steps: int, noise: float, model_name
) -> tuple[str, bool]:
    """一条链：连续变异 steps 轮。中途失败则停在上一状态（终态仍有效）。"""
    cif = seed_cif
    completed = True
    for step in range(steps):
        try:
            r = call_async_tool(
                eps["mutate"], "mutate_structure_async", "get_mutate_result",
                {"t": noise, "cif_content": cif, "model_name": model_name,
                 "name": f"{chain_id}_s{step}"},
                total_wait=int(os.environ.get("MUTATE_WAIT", "300")), interval=5.0,
            )
        except Exception as e:
            print(f"  [{chain_id}] 步 {step} 异常: {e}；链停在该步")
            completed = False
            break
        child = r.get("child_cif") if isinstance(r, dict) else None
        if not child:
            print(f"  [{chain_id}] 步 {step} 变异失败: {str(r)[:80]}；链停在该步")
            completed = False
            break
        cif = child
    return cif, completed


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--rep", required=True, type=int, help="重复序号（决定种子抽样与 RNG 种子）")
    p.add_argument("--seeds", required=True, help="逗号分隔的种子 CIF 路径")
    p.add_argument("--chains", type=int, default=20)
    p.add_argument("--steps", type=int, default=10)
    p.add_argument("--noise", type=float, default=0.3)
    p.add_argument("--model-name", default="mattergen_base")  # 与 evolve_driver 同源默认
    args = p.parse_args()

    seeds = [Path(s.strip()) for s in args.seeds.split(",") if s.strip()]
    if not seeds:
        sys.exit("--seeds 为空")
    if args.chains > len(seeds):
        sys.exit(f"--chains({args.chains}) > 种子数({len(seeds)})：无重复抽样不可行")

    # rep 决定抽样：同一 rep 重跑可复现，不同 rep 抽不同子集
    rng = random.Random(20260902 + args.rep)
    chain_seeds = rng.sample(seeds, args.chains)

    run_dir: Path = args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    eps = get_endpoints()

    finals: list[tuple[str, str]] = []  # (cif_text, name)
    n_full = 0
    for i, seed in enumerate(chain_seeds):
        name = f"rw{args.rep}_chain{i}"
        print(f"==> 链 {i+1}/{args.chains} 种子={seed.name}")
        cif, completed = run_chain(eps, seed.read_text(encoding="utf-8"),
                                   name, args.steps, args.noise, args.model_name)
        finals.append((cif, name))
        n_full += completed
    print(f"==> {args.chains} 条链完成（走满 {args.steps} 步: {n_full}）")

    # 终态表征（与 EA 臂同协议：mattergen_eval batch-of-1，relax+evaluate）
    backend = get_backend("mattergen_eval")
    inds = backend(finals, EVAL_KEYS, eps, tag=f"rw{args.rep}")

    metrics = []
    for (cif, name), ind in zip(finals, inds):
        (run_dir / f"{name}_final.cif").write_text(cif, encoding="utf-8")
        metrics.append({k: ind.get(k) for k in METRIC_OUT_KEYS})

    (run_dir / "per_structure_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    n_valid_metrics = sum(1 for m in metrics if m.get("ehull_eval") is not None)
    print(f"==> 落盘 {len(metrics)} 个终态（表征成功 {n_valid_metrics}）→ {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

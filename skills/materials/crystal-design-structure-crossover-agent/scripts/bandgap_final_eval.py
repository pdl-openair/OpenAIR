#!/usr/bin/env python3
"""steering_parity2 实验二终评管线(判级专用,与 fitness 解耦)。

对每个 EA run 目录:
  1. 终态池 = 末代 population ∪ 历代 pareto_rank==1 且不在末代者(至多补 20,
     近世代优先) —— 与 parity_metrics._collect_ea 主口径逐字一致;
  2. 逐结构 mattersim-relax(与 fitness 期同一 relax 协议)→ 弛豫后 CIF;
  3. alignn bandgap 复核(弛豫后结构上) + mattergen-evaluate 批量
     (relax=true → novel / ehull / stable, per_structure 对齐);
  4. 写 <run-dir>/per_structure_final_metrics.json(含 cif 文本,供池化
     uniqueness 判定)。

幂等:输出文件已存在则跳过(--force 重算)。MCP 端点走环境变量
(MCP_*_URL),与 run.sh 的 .env 优先语义一致。

用法:
  python3 bandgap_final_eval.py --run-dir <dir> [--run-dir <dir> ...] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from evaluators import cif_to_structure, relax_cif  # noqa: E402
from mcp_client import call_tool, call_async_tool, get_endpoints  # noqa: E402
from parity_metrics import _collect_ea  # noqa: E402

INBAND_LO, INBAND_HI = 2.7, 3.3      # 判级带宽(预注册,含 alignn ±0.1-0.3 eV 余量)
METASTABLE_EHULL = 0.1               # mSUN stable 判据(eV/atom)


def evaluate_batch(cifs: list[str], eps: dict, tag: str) -> list[dict | None]:
    """分块批量 evaluate(relax=true),返回与 cifs 等长的 per_structure 行列表。"""
    rows: list[dict | None] = [None] * len(cifs)
    batch = max(1, int(os.environ.get("EVALUATE_BATCH_SIZE", "20")))
    for c0 in range(0, len(cifs), batch):
        chunk = list(range(c0, min(c0 + batch, len(cifs))))
        print(f"  [{tag}] evaluate 批量 {c0 // batch + 1}"
              f"/{(len(cifs) + batch - 1) // batch}({len(chunk)} 个)")
        try:
            res = call_async_tool(
                eps["evaluate"], "evaluate_structures_async", "get_evaluation_result",
                {"structures": [{"cif_content": cifs[p]} for p in chunk], "relax": True},
                total_wait=int(os.environ.get("EVALUATE_WAIT", "900")) * len(chunk),
                interval=10.0,
            )
        except Exception as e:
            print(f"  [{tag}] evaluate 批量异常: {e}")
            continue
        by_idx: dict[int, dict] = {}
        for r in (res or {}).get("per_structure") or []:
            if isinstance(r, dict) and isinstance(r.get("index"), int):
                by_idx[r["index"]] = r
        for pos, p in enumerate(chunk):
            row = by_idx.get(pos)
            if row is not None:
                rows[p] = {
                    "ehull_eval": row.get("energy_above_hull_per_atom"),
                    "novel": None if row.get("novel") is None else float(bool(row["novel"])),
                    "stable": None if row.get("stable") is None else float(bool(row["stable"])),
                    "structure_valid": None if row.get("structure_validity") is None
                    else float(bool(row["structure_validity"])),
                    "comp_valid": None if row.get("comp_validity") is None
                    else float(bool(row["comp_validity"])),
                }
    return rows


def final_eval_run(run_dir: Path, eps: dict, force: bool = False) -> dict:
    out_path = run_dir / "per_structure_final_metrics.json"
    if out_path.exists() and not force:
        print(f"==> {run_dir.name}: 终评已存在,跳过(--force 重算)")
        return json.loads(out_path.read_text(encoding="utf-8"))

    gen_files = sorted(run_dir.glob("population_gen*.json"))
    if not gen_files:
        sys.exit(f"{run_dir}: 无 population_gen*.json,先跑完 EA run")
    samples = _collect_ea(run_dir, gen_files, hof_only=False)
    print(f"==> {run_dir.name}: 终态池 {len(samples)} 个结构")

    # 1) 逐结构 relax(与 fitness 期同协议;失败回退原始 cif)
    relaxed: list[str] = []
    for i, s in enumerate(samples):
        rc, _ = relax_cif(s["cif"], eps, name=str(s.get("name") or f"ind{i}"))
        relaxed.append(rc)

    # 2) alignn bandgap 复核(弛豫后结构)
    bandgaps: list[float | None] = []
    for i, rc in enumerate(relaxed):
        try:
            res = call_tool(eps["alignn"], "predict_alignn_properties",
                            {"cif_content": rc, "models": ["bandgap"],
                             "name": str(samples[i].get("name") or "ind")},
                            timeout=180)
            env = (res or {}).get("properties", {}).get("bandgap", {})
            bandgaps.append(env.get("value") if isinstance(env, dict) else None)
        except Exception as e:
            print(f"  [alignn] {samples[i].get('name')}: 异常 {e}")
            bandgaps.append(None)

    # 3) evaluate 批量(novel / ehull / stable)
    rows = evaluate_batch(relaxed, eps, tag=run_dir.name)

    # 4) 汇总
    out = []
    for i, s in enumerate(samples):
        bg = bandgaps[i]
        row = rows[i] or {}
        ehull = row.get("ehull_eval")
        rec = {
            "name": s.get("name"),
            "source_gen_file": None,  # _collect_ea 不回溯来源,池内身份以 name 为准
            "bandgap_alignn": bg,
            "inband": (None if bg is None else float(INBAND_LO <= bg <= INBAND_HI)),
            "ehull_eval": ehull,
            "metastable": (None if ehull is None else float(ehull < METASTABLE_EHULL)),
            "novel": row.get("novel"),
            "stable": row.get("stable"),
            "structure_valid": row.get("structure_valid"),
            "comp_valid": row.get("comp_valid"),
            "formula": None,
            "cif": relaxed[i],
        }
        try:
            rec["formula"] = cif_to_structure(rc).composition.reduced_formula
        except Exception:
            pass
        out.append(rec)

    n_inband = sum(1 for r in out if r["inband"] == 1.0)
    payload = {
        "scheme": "steering_parity2_final_eval",
        "run_dir": str(run_dir),
        "inband_range": [INBAND_LO, INBAND_HI],
        "metastable_ehull": METASTABLE_EHULL,
        "n_terminal_pool": len(out),
        "n_inband": n_inband,
        "structures": out,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"==> {run_dir.name}: 写出 {out_path.name}"
          f"(带内 {n_inband}/{len(out)})")
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", action="append", required=True, type=Path)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    eps = get_endpoints()
    for rd in args.run_dir:
        final_eval_run(rd, eps, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

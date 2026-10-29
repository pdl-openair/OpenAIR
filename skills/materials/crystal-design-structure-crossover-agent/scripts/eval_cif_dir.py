# scripts/eval_cif_dir.py
"""对目录内全部 CIF 做逐体评估，写 per_structure_metrics.json（parity_metrics 臂摄入格式）。

用途：evo_sun_parity 阶段 0/1 —— fresh_gen 的 200 个原始采样、或任何只有 CIF
的臂目录补齐逐体指标。协议与 EA/random_walk 臂一致：mattergen_eval backend，
batch-of-1（relax + evaluate_structures），逐条落盘、可断点续跑（已完成的
条目重跑时跳过，适合长任务被杀后恢复）。

用法：
  python3 eval_cif_dir.py --dir <臂目录> [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evaluators import get_backend  # noqa: E402
from mcp_client import get_endpoints  # noqa: E402
from random_walk_runner import EVAL_KEYS, METRIC_OUT_KEYS  # noqa: E402

METRICS_FILE = "per_structure_metrics.json"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", required=True, type=Path, help="含 *.cif 的臂目录")
    p.add_argument("--limit", type=int, default=0, help="只评前 N 个（调试用；0=全部）")
    args = p.parse_args()

    d: Path = args.dir
    cifs = sorted(d.glob("*.cif"))
    if not cifs:
        sys.exit(f"目录 {d} 无 CIF")
    if args.limit:
        cifs = cifs[: args.limit]

    # 断点续跑：已有条目跳过
    done: set[str] = set()
    metrics: list[dict] = []
    if (d / METRICS_FILE).exists():
        metrics = json.loads((d / METRICS_FILE).read_text(encoding="utf-8"))
        done = {m.get("name") for m in metrics}
    todo = [p for p in cifs if p.stem not in done]
    print(f"==> {d}: 共 {len(cifs)} 个 CIF，已完成 {len(done)}，待评 {len(todo)}")

    eps = get_endpoints()
    backend = get_backend("mattergen_eval")

    for k, cif_path in enumerate(todo):
        name = cif_path.stem
        items = [(cif_path.read_text(encoding="utf-8"), name)]
        try:
            inds = backend(items, EVAL_KEYS, eps, tag=f"{k+1}/{len(todo)}")
            ind = inds[0]
        except Exception as e:
            print(f"   [{name}] 评估异常: {e}")
            ind = {"name": name}
        entry = {key: ind.get(key) for key in METRIC_OUT_KEYS}
        entry["name"] = name
        metrics.append(entry)
        (d / METRICS_FILE).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    n_ok = sum(1 for m in metrics if m.get("ehull_eval") is not None)
    print(f"==> 完成：{len(metrics)} 条（表征成功 {n_ok}）→ {d / METRICS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

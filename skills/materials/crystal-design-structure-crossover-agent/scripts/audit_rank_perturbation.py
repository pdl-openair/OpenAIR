#!/usr/bin/env python3
"""steering_parity3 冒烟期旋钮秩扰动审计(预注册 §0.1,审计不过的杠杆不得入臂)。

对每个杠杆构造双场景:同一合成种群,仅改变该杠杆取值,跑
saes_selection.evolutionary_environmental_selection,断言「选择结果
(存活集合或排序)发生变化」—— 即该杠杆能扰动选择秩,不是假旋钮。

  ① noise   : 选择层无 noise,它作用于子代生成(diffusion)—— 冒烟时用
              MCP 双 t 值(t=0.1 vs 0.5)对同一 parent 各 mutate 一次,
              断言子代 CIF 不同(parity-2 已实证,此处留 --with-mcp 复核)。
  ③ 边界    : bandgap 约束 [2.7,3.3] vs 微调 [2.4,3.6] → 违反判定变化 → 选择不同。
  ⑤ 化学空间: element_set allowed_only 两个集合 → 违反按越界元素数非均匀变化 → 选择不同。

用法: python audit_rank_perturbation.py [--with-mcp] [--cif <parent.cif>]
退出码 0 = 全部杠杆通过;非 0 = 有杠杆未通过(不得入臂)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from saes_selection import evolutionary_environmental_selection  # noqa: E402


def _synth_pop(n: int = 20) -> list:
    """合成种群:ehull/fitness/bandgap/element_set 逐个体错开,无结构对象。

    只喂选择层需要的标量键;bandgap 沿 2.0-4.0 铺开,使边界平移必然
    改变部分个体的违反判定;element_set 混入 Zr/Hf/Y,使 allow_only
    换集合时违反计数非均匀变化。
    """
    pop = []
    for i in range(n):
        pop.append({
            "name": f"s{i}",
            "ehull": 0.02 + 0.01 * i,           # 单目标 fitness 用
            "bandgap": 2.0 + 0.105 * i,          # 2.0 .. 4.0
            "num_elements": 2 + (i % 3),
            "comp_valid": True,
            "element_set": [["Si", "O"], ["Zr", "O"], ["Y", "N"], ["Hf", "O"]][i % 4],
            "cif": None,                         # 选择层标量路径不解析 cif
        })
    return pop


def _select(pop: list, constraints: dict) -> list:
    return evolutionary_environmental_selection(
        [dict(x) for x in pop], constraints, {"ehull": "minimize"}, pop_size=10)


def _selected_key(sel: list) -> str:
    return ",".join(sorted(x["name"] for x in sel))


def audit_boundary() -> bool:
    """杠杆③:同一群,名义带 vs 微调带 → 选择集合不同。"""
    pop = _synth_pop()
    nominal = {"bandgap": {"min": 2.7, "max": 3.3, "weight": 10},
               "num_elements": {"min": 2, "weight": 10},
               "comp_valid": {"expected": True, "weight": 10}}
    widened = json.loads(json.dumps(nominal))
    widened["bandgap"] = {"min": 2.4, "max": 3.6, "weight": 10}
    a, b = _select(pop, nominal), _select(pop, widened)
    ok = _selected_key(a) != _selected_key(b)
    print(f"  ③ boundary: nominal={_selected_key(a)} widened={_selected_key(b)} -> "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def audit_allow_only() -> bool:
    """杠杆⑤:allowed_only 两集合 → 选择集合不同。"""
    pop = _synth_pop()
    base = {"bandgap": {"min": 2.7, "max": 3.3, "weight": 10},
            "num_elements": {"min": 2, "weight": 10},
            "comp_valid": {"expected": True, "weight": 10}}
    s1 = json.loads(json.dumps(base))
    s1["element_set"] = {"allowed_only": ["Si", "Zr", "Y"], "weight": 10}
    s2 = json.loads(json.dumps(base))
    s2["element_set"] = {"allowed_only": ["Si", "Zr", "Y", "Hf"], "weight": 10}
    a, b = _select(pop, s1), _select(pop, s2)
    ok = _selected_key(a) != _selected_key(b)
    print(f"  ⑤ allow_only: 3elem={_selected_key(a)} 4elem={_selected_key(b)} -> "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def audit_noise_mcp(cif_path: str) -> bool:
    """杠杆①(经验复核):同 parent,t=0.1 vs 0.5 各 mutate 一次 → 子代 CIF 不同。"""
    from mcp_client import call_async_tool, get_endpoints

    eps = get_endpoints()
    cif = Path(cif_path).read_text(encoding="utf-8")
    children = {}
    for t in (0.1, 0.5):
        r = call_async_tool(
            eps["mutate"], "mutate_structure_async", "get_mutate_result",
            {"t": t, "cif_content": cif, "name": f"audit_t{t}"},
            total_wait=300, interval=5.0,
        )
        children[t] = (r or {}).get("child_cif")
    a, b = children[0.1], children[0.5]
    ok = bool(a and b and a.strip() != b.strip())
    lens = {t: (len(c) if c else 0) for t, c in children.items()}
    print(f"  ① noise(MCP): t=0.1 len={lens[0.1]} t=0.5 len={lens[0.5]} -> "
          f"{'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--with-mcp", action="store_true",
                   help="附加 ①noise 的 MCP 双 t 值经验复核(需 mutate 服务在线)")
    p.add_argument("--cif", default=str(
        _HERE.parents[2] / "mattergen-results/mattergen-base-200"
        / "steering_parity2/seeds.txt"),
                   help="① 复核用的 parent CIF 路径(--with-mcp 时)")
    args = p.parse_args()

    print("== steering_parity3 旋钮秩扰动审计 ==")
    results = {"boundary_3": audit_boundary(), "allow_only_5": audit_allow_only()}
    if args.with_mcp:
        # seeds.txt 第一行是仓库相对的 CIF 路径,取第一个存在的
        first_cif = args.cif
        if first_cif.endswith("seeds.txt") and Path(first_cif).exists():
            repo = Path(first_cif).parents[3]
            for line in Path(first_cif).read_text().splitlines():
                if line.strip():
                    cand = repo / line.strip()
                    if cand.exists():
                        first_cif = str(cand)
                        break
        results["noise_1_mcp"] = audit_noise_mcp(first_cif)

    ok_all = all(results.values())
    print(json.dumps(results, ensure_ascii=False))
    print("== 审计结果:", "全部通过" if ok_all else "存在未通过杠杆 —— 不得入臂 ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
MatterGen 结构评估 MCP —— 集成冒烟测试（异步 submit + poll）。

覆盖：
1. MCP 握手 + tools/list 含 evaluate_structures_async / get_evaluation_result
2. evaluate_structures_async(structures=[{cif_content}], ...) 提交
3. 轮询 get_evaluation_result 直到 done
4. 断言 result.metrics 非空、含 frac_novel_structures / frac_unique_structures /
   frac_novel_unique_stable_structures(SUN) / precision，并打印 S/U/N/SUN 四件套

⚠️ 评估要对 845,997 结构的 alex-mp 参考集做结构匹配；常见元素(NaCl/Si)单结构
   匹配需数分钟～20+ 分钟。

关于 SUN（frac_novel_unique_stable_structures = Stable ∧ Unique ∧ Novel，论文头条指标）：
- 默认 relax=False + 自带 energies=[0.0]：跳过 GPU 弛豫，隔离"参考集匹配 + 指标"管线。
  此时 SUN 的 Stable 分量是占位能量算出的、无物理意义，SUN 一般为 0/不可信 —— 仅校验它"存在"。
- 设 MATTERGEN_EVAL_RELAX=1 走 relax=True：MatterSim 真实弛豫重算能量，此时
  SUN / frac_stable_structures / avg_energy_above_hull_per_atom 才有意义（需 GPU，更慢）。
  想看到非零 SUN，需喂一批"参考集里没有的生成结构"，单结构冒烟用例（如 NaCl）通常为 0。

用法：
  python tests/test_mattergen-evaluate.py
  MATTERGEN_EVAL_MCP_URL=http://localhost:9130/mcp python tests/test_mattergen-evaluate.py
  MATTERGEN_EVAL_RELAX=1 python tests/test_mattergen-evaluate.py     # 跑真实弛豫(SUN 有意义)
  python tests/test_mattergen-evaluate.py --cif /path/to/some.cif
"""
from __future__ import annotations

import os
import sys

from skills.materials.Materials_MCP.test_MCPs.mcp_client import MCPClient, MCPError, cif_content_from, env_url, ensure_self_on_path

ensure_self_on_path()

DEFAULT_URL = os.environ.get("MATTERGEN_EVALUATE_MCP_URL", "")
POLL_TIMEOUT = float(os.environ.get("MATTERGEN_EVAL_POLL_TIMEOUT", "1800"))
# 1=走 relax=True（MatterSim 真实弛豫，SUN/稳定性指标有意义，需 GPU 更慢）；
# 0/未设=relax=False + 占位 energies（跳过 GPU，只验结构指标 + 参考集匹配管线）。
RELAX = os.environ.get("MATTERGEN_EVAL_RELAX", "0").lower() in ("1", "true", "yes")

# 内置示例 CIF：NaCl 岩盐（2 原子 primitive cell，P1）。
# Na/Cl 都是 MP2020 校正覆盖的常见元素；NaCl 在 alex-mp 参考集里恰好 1 条匹配
# （recall = 1/845997），是稳定的冒烟用例（但匹配仍需数分钟～十几分钟）。
DEFAULT_NACL_CIF = """data_NaCl
_cell_length_a 5.64
_cell_length_b 5.64
_cell_length_c 5.64
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Na 0.0 0.0 0.0
Cl1 Cl 0.5 0.5 0.5
"""


def main() -> int:
    url = env_url(DEFAULT_URL, "MATTERGEN_EVAL_MCP_URL", "MCP_URL")
    print(f"[mattergen-eval] 目标: {url}")
    client = MCPClient(url, timeout=60.0)

    # ---- 1. 握手 + tools/list ----
    try:
        info = client.initialize()
        print(f"[mattergen-eval] 握手 OK: server={info.get('serverInfo', {}).get('name')}")
        tools = {t["name"] for t in client.list_tools()}
        for need in ("evaluate_structures_async", "get_evaluation_result"):
            assert need in tools, f"tools/list 缺少 {need}: {tools}"
        print(f"[mattergen-eval] tools/list OK: {sorted(tools)}")
    except Exception as e:  # noqa: BLE001
        print(f"[mattergen-eval] ✗ 握手/list 失败: {e}")
        return 1

    # ---- 2. 取 CIF（--cif / MATTERGEN_EVAL_CIF / 内置 NaCl）----
    cif_content = cif_content_from("MATTERGEN_EVAL_CIF", "--cif", DEFAULT_NACL_CIF)
    assert cif_content, "CIF 正文为空"

    # ---- 3. 异步提交 + 轮询 ----
    # RELAX=0：relax=False + energies=[0.0]，跳过 GPU 弛豫，专注验证"参考集匹配 + 指标"管线
    #   （energy=0 是占位值，稳定性/SUN 分量无物理意义，仅校验 key 存在）。
    # RELAX=1：relax=True，MatterSim 真实弛豫重算能量，SUN/frac_stable/avg_energy_above_hull 才有意义。
    submit_args = {"structures": [{"cif_content": cif_content}]}
    if RELAX:
        submit_args["relax"] = True
        print("[mattergen-eval] 模式: relax=True（真实弛豫，SUN 有意义，需 GPU 更慢）")
    else:
        submit_args["relax"] = False
        submit_args["energies"] = [0.0]
        print("[mattergen-eval] 模式: relax=False（跳过 GPU，SUN 稳定分量为占位、仅验存在）")
    try:
        done = client.call_tool_async(
            "evaluate_structures_async",
            submit_args,
            "get_evaluation_result",
            interval=10.0,
            timeout=POLL_TIMEOUT,
        )
        result = done.get("result")
        assert isinstance(result, dict), f"result 非 dict: {result!r}"
        # 引擎软失败用 {value:null, error}
        if result.get("value") is None and result.get("error"):
            print(f"[mattergen-eval] ✗ 评估返回错误: {result['error']}")
            return 1

        metrics = result.get("metrics")
        assert isinstance(metrics, dict) and metrics, f"metrics 为空: {result!r}"
        # S/U/N/SUN 四件套 + precision 都应存在（energy 能力在有 energies 时总会加上）。
        for key in (
            "frac_novel_structures",          # N
            "frac_unique_structures",         # U
            "frac_novel_unique_structures",   # N∧U
            "frac_novel_unique_stable_structures",  # SUN = S∧U∧N
            "frac_stable_structures",         # S
            "precision",
        ):
            assert key in metrics, f"metrics 缺少 {key}: {metrics!r}"

        print(
            f"[mattergen-eval] 评估 OK: num_evaluated={result.get('num_evaluated')}, "
            f"parse_errors={result.get('n_failed_jobs')}, elapsed={done.get('elapsed_sec')}s"
        )
        # 论文头条四件套：S / U / N / SUN，外加 precision/recall 与能量稳定性。
        print(
            f"[mattergen-eval]   · S={metrics.get('frac_stable_structures')}  "
            f"U={metrics.get('frac_unique_structures')}  "
            f"N={metrics.get('frac_novel_structures')}  "
            f"SUN={metrics.get('frac_novel_unique_stable_structures')}"
        )
        print(
            f"[mattergen-eval]   · precision={metrics.get('precision')}, "
            f"recall={metrics.get('recall')}, "
            f"avg_energy_above_hull={metrics.get('avg_energy_above_hull_per_atom')} eV/atom"
        )
    except AssertionError as e:
        print(f"[mattergen-eval] ✗ 断言失败: {e}")
        return 1
    except MCPError as e:
        print(f"[mattergen-eval] ✗ MCP 错误: {e}")
        return 1

    print("[mattergen-eval] ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

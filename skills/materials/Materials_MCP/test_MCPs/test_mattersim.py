#!/usr/bin/env python3
"""
MatterSim 结构弛豫 MCP —— 集成冒烟测试（异步 submit + poll）。

覆盖：
1. MCP 握手 + tools/list 含 relax_structure_async / get_relax_result
2. relax_structure_async(cif_content=<Si 原胞>) 提交
3. 轮询 get_relax_result 直到 done
4. 断言 result.relaxed_cif 非空、total_energy_eV 为有限数

⚠️ MatterSim 弛豫预计数十秒，需 GPU；checkpoint 须已在容器内
（bind-mount /opt/mattersim/mattersim-v1.0.0-1M.pth）。

用法：
  python test_mattersim.py
  MATTERSIM_MCP_URL=http://localhost:9129/mcp python test_mattersim.py
  MATTERSIM_CIF_PATH=/path/to/structure.cif python test_mattersim.py   # 自定义 CIF
"""
from __future__ import annotations

import math
import os
import sys

from skills.materials.Materials_MCP.test_MCPs.mcp_client import MCPClient, MCPError, env_url

DEFAULT_URL = os.environ.get("MATTERSIM_MCP_URL", "")
POLL_TIMEOUT = float(os.environ.get("MATTERSIM_POLL_TIMEOUT", "240"))

# 常规立方 Si（8 原子），用于弛豫冒烟。a≈5.43Å。
SI_CIF = """data_Si
_cell_length_a 5.43
_cell_length_b 5.43
_cell_length_c 5.43
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Si1 Si 0.00 0.00 0.00
Si2 Si 0.25 0.25 0.25
Si3 Si 0.50 0.00 0.50
Si4 Si 0.00 0.50 0.50
Si5 Si 0.50 0.50 0.00
Si6 Si 0.75 0.75 0.25
Si7 Si 0.25 0.75 0.75
Si8 Si 0.75 0.25 0.75
"""


def _load_cif() -> tuple[str, str]:
    """返回 (cif_content, name)。设了 MATTERSIM_CIF_PATH 则读文件，否则用内置 SI_CIF。"""
    path = os.environ.get("MATTERSIM_CIF_PATH")
    if not path:
        print("[mattersim] 未设 MATTERSIM_CIF_PATH，使用内置 Si 原胞")
        return SI_CIF, "Si_test"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    print(f"[mattersim] 使用 CIF 文件: {path}（name={name}, {len(content)} 字节）")
    return content, name


def main() -> int:
    url = env_url(DEFAULT_URL, "MATTERSIM_MCP_URL", "MCP_URL")
    print(f"[mattersim] 目标: {url}")
    client = MCPClient(url, timeout=60.0)

    try:
        info = client.initialize()
        print(f"[mattersim] 握手 OK: server={info.get('serverInfo', {}).get('name')}")
        tools = {t["name"] for t in client.list_tools()}
        for need in ("relax_structure_async", "get_relax_result"):
            assert need in tools, f"tools/list 缺少 {need}: {tools}"
        print(f"[mattersim] tools/list OK: {sorted(tools)}")
    except Exception as e:  # noqa: BLE001
        print(f"[mattersim] ✗ 握手/list 失败: {e}")
        return 1

    cif_content, name = _load_cif()
    try:
        done = client.call_tool_async(
            "relax_structure_async",
            {"cif_content": cif_content, "name": name, "max_steps": 30},
            "get_relax_result",
            interval=5.0,
            timeout=POLL_TIMEOUT,
        )
        print(done)
        result = done.get("result")
        assert isinstance(result, dict), f"result 非 dict: {result!r}"
        # 弛豫软失败：{value:null, error}
        if result.get("value") is None and result.get("error"):
            print(f"[mattersim] ✗ 弛豫返回错误: {result['error']}")
            return 1
        assert result.get("relaxed_cif"), f"缺 relaxed_cif: {result!r}"
        e_pa = result.get("energy_per_atom_eV")
        assert e_pa is not None and math.isfinite(e_pa), f"energy_per_atom 非法: {e_pa!r}"
        print(
            f"[mattersim] 弛豫 OK: atoms={result.get('num_atoms')}, "
            f"E/atom={e_pa:.4f} eV, f_max={result.get('f_max_eV_A')}, "
            f"elapsed={done.get('elapsed_sec')}s"
        )
    except AssertionError as e:
        print(f"[mattersim] ✗ 断言失败: {e}")
        return 1
    except MCPError as e:
        print(f"[mattersim] ✗ MCP 错误: {e}")
        return 1

    print("[mattersim] ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

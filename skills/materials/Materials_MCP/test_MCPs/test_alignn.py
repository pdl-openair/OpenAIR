#!/usr/bin/env python3
"""
ALIGNN 性质预测 MCP —— 集成冒烟测试。

覆盖：
1. MCP 握手（initialize）
2. tools/list 含 predict_alignn_properties
3. 调用 predict_alignn_properties（默认 CrSiTe3 演示结构，跑 bandgap/formation_energy/magmom）
   - 断言返回 properties 结构正确
   - 断言失败语义为 {value:null, error}（不出现 999/-1 哨兵）

用法：
  python test_alignn.py                                # 默认公网
  ALIGNN_MCP_URL=http://localhost:9125/mcp python test_alignn.py
  ALIGNN_CIF_PATH=/path/to/structure.cif python test_alignn.py   # 用自定义 CIF
"""
from __future__ import annotations

import os
import sys

from skills.materials.Materials_MCP.test_MCPs.mcp_client import MCPClient, MCPError, env_url

DEFAULT_URL = os.environ.get("ALIGNN_MCP_URL", "")
TOOL = "predict_alignn_properties"
EXPECTED_MODELS = {"formation_energy", "bandgap", "dielectric", "ehull"}


def _load_cif_args() -> dict:
    """从 ALIGNN_CIF_PATH 读取 CIF 全文；未设置则返回空 dict（走服务端默认 CrSiTe3）。"""
    path = os.environ.get("ALIGNN_CIF_PATH")
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    print(f"[alignn] 使用 CIF 文件: {path}（name={name}, {len(content)} 字节）")
    return {"cif_content": content, "name": name}


def main() -> int:
    url = env_url(DEFAULT_URL, "ALIGNN_MCP_URL", "MCP_URL")
    print(f"[alignn] 目标: {url}")
    client = MCPClient(url, timeout=120.0)
    failures: list[str] = []

    # 1) 握手
    try:
        info = client.initialize()
        print(f"[alignn] 握手 OK: server={info.get('serverInfo', {}).get('name')}")
    except (MCPError, Exception) as e:  # noqa: BLE001
        print(f"[alignn] ✗ 握手失败: {e}")
        return 1

    # 2) tools/list
    try:
        tools = client.list_tools()
        names = {t["name"] for t in tools}
        assert TOOL in names, f"tools/list 缺少 {TOOL}: {names}"
        print(f"[alignn] tools/list OK: {sorted(names)}")
    except Exception as e:  # noqa: BLE001
        print(f"[alignn] ✗ tools/list 失败: {e}")
        return 1

    # 3) 调用工具（默认 CIF = CrSiTe3；设了 ALIGNN_CIF_PATH 则用文件内容）
    try:
        call_args = {**_load_cif_args(), "models": ["formation_energy", "bandgap", "magmom"]}
        if "name" not in call_args:
            print("[alignn] 未设 ALIGNN_CIF_PATH，走服务端默认 CrSiTe3")
        res = client.call_tool(TOOL, call_args)
        assert isinstance(res, dict), f"返回非 dict: {res!r}"
        assert "properties" in res, f"缺少 properties: {res!r}"
        props = res["properties"]
        print(f"[alignn] 调用 OK: name={res.get('name')}, models={sorted(props)}")

        # 4) 失败语义校验：每个模型项要么有 value，要么 {value:null, error}
        for m, v in props.items():
            assert isinstance(v, dict), f"{m} 结果非 dict: {v!r}"
            assert "value" in v, f"{m} 缺 value 字段: {v!r}"
            if v.get("value") is None:
                assert v.get("error"), f"{m} value=null 但缺 error: {v!r}"
                print(f"[alignn]   · {m}: 失败（预期可恢复）error={str(v['error'])[:80]}")
            else:
                print(f"[alignn]   · {m}: value={v['value']}")
                # 哨兵值绝不应出现为成功值
                assert v["value"] not in (999, -1, 999.0, -1.0), f"{m} 返回哨兵值: {v}"
    except AssertionError as e:
        print(f"[alignn] ✗ 调用/断言失败: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[alignn] ✗ 调用异常: {e}")
        return 1

    print(f"[alignn] ✓ 全部通过{f'；失败项: {failures}' if failures else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
DiffCSP 晶体结构预测 MCP —— 集成冒烟测试（异步 submit + poll）。

覆盖：
1. MCP 握手 + tools/list 含 generate_structures_async / get_generation_result
2. generate_structures_async(model_name=mp_csp, formula='GaTe', num_evals=1) 提交
3. 轮询 get_generation_result 直到 done
4. 断言 result.structures 非空、首项含 cif（1000 步 CSP 采样）

⚠️ 1000 步采样，预计数十秒到数分钟，需 GPU（diffcsp-infer sidecar，宿主端口 9128）。
本测试只打 MCP shim（9127）；sidecar 由部署侧保证在跑。

用法：
  python test_diffcsp.py
  DIFFCSP_MCP_URL=http://localhost:9127/mcp python test_diffcsp.py
  DIFFCSP_FORMULA=SrTiO3 python test_diffcsp.py
"""
from __future__ import annotations

import os
import sys

from skills.materials.Materials_MCP.test_MCPs.mcp_client import MCPClient, MCPError, env_url

DEFAULT_URL = os.environ.get("DIFFCSP_MCP_URL", "")
POLL_TIMEOUT = float(os.environ.get("DIFFCSP_POLL_TIMEOUT", "600"))
FORMULA = os.environ.get("DIFFCSP_FORMULA", "GaTe")


def main() -> int:
    url = env_url(DEFAULT_URL, "DIFFCSP_MCP_URL", "MCP_URL")
    print(f"[diffcsp] 目标: {url}  formula={FORMULA}")
    client = MCPClient(url, timeout=60.0)

    try:
        info = client.initialize()
        print(f"[diffcsp] 握手 OK: server={info.get('serverInfo', {}).get('name')}")
        tools = {t["name"] for t in client.list_tools()}
        for need in ("generate_structures_async", "get_generation_result"):
            assert need in tools, f"tools/list 缺少 {need}: {tools}"
        print(f"[diffcsp] tools/list OK: {sorted(tools)}")
    except Exception as e:  # noqa: BLE001
        print(f"[diffcsp] ✗ 握手/list 失败: {e}")
        return 1

    try:
        done = client.call_tool_async(
            "generate_structures_async",
            {"model_name": "mp_csp", "formula": FORMULA, "num_evals": 1},
            "get_generation_result",
            interval=8.0,
            timeout=POLL_TIMEOUT,
        )
        print(done)
        result = done.get("result")
        assert isinstance(result, dict), f"result 非 dict: {result!r}"
        # sidecar 失败标记 done 但 {value:null, error}
        if result.get("value") is None and result.get("error"):
            print(f"[diffcsp] ✗ 生成返回错误: {result['error']}")
            return 1
        structs = result.get("structures") or []
        assert structs, f"structures 为空: {result!r}"
        print(
            f"[diffcsp] 生成 OK: num_generated={result.get('num_generated')}, "
            f"failed={result.get('num_failed')}, elapsed={done.get('elapsed_sec')}s"
        )
        s0 = structs[0]
        assert s0.get("cif"), f"首个结构缺 cif: {s0!r}"
        print(f"[diffcsp]   · [0] formula={s0.get('formula')}, "
              f"sg={s0.get('spacegroup')}, atoms={s0.get('num_atoms')}")
    except AssertionError as e:
        print(f"[diffcsp] ✗ 断言失败: {e}")
        return 1
    except MCPError as e:
        print(f"[diffcsp] ✗ MCP 错误: {e}")
        return 1

    print("[diffcsp] ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

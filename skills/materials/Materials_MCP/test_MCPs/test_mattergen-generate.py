#!/usr/bin/env python3
"""
MatterGen 结构生成 MCP —— 集成冒烟测试（异步 submit + poll）。

覆盖：
1. MCP 握手 + tools/list 含 generate_crystals_async / get_generation_result
2. generate_crystals_async(model_name=mattergen_base, num_samples=1) 提交
3. 轮询 get_generation_result 直到 done
4. 断言 result.structures 非空、至少 1 个结构带 formula

⚠️ 单次生成固定约 2 分钟，整个测试预计 ~2–3 分钟，需 GPU。

用法：
  python test_mattergen.py
  MATTERGEN_MCP_URL=http://localhost:9126/mcp python test_mattergen.py
"""
from __future__ import annotations

import os
import sys

from skills.materials.Materials_MCP.test_MCPs.mcp_client import MCPClient, MCPError, env_url

DEFAULT_URL = os.environ.get("MATTERGEN_MCP_URL", "")
POLL_TIMEOUT = float(os.environ.get("MATTERGEN_POLL_TIMEOUT", "300"))


def main() -> int:
    url = env_url(DEFAULT_URL, "MATTERGEN_MCP_URL", "MCP_URL")
    print(f"[mattergen] 目标: {url}")
    client = MCPClient(url, timeout=60.0)

    try:
        info = client.initialize()
        print(f"[mattergen] 握手 OK: server={info.get('serverInfo', {}).get('name')}")
        tools = {t["name"] for t in client.list_tools()}
        for need in ("generate_crystals_async", "get_generation_result"):
            assert need in tools, f"tools/list 缺少 {need}: {tools}"
        print(f"[mattergen] tools/list OK: {sorted(tools)}")
    except Exception as e:  # noqa: BLE001
        print(f"[mattergen] ✗ 握手/list 失败: {e}")
        return 1

    try:
        done = client.call_tool_async(
            "generate_crystals_async",
            {"model_name": "mattergen_base", "num_samples": 1},
            "get_generation_result",
            interval=8.0,
            timeout=POLL_TIMEOUT,
        )
        result = done.get("result")
        assert isinstance(result, dict), f"result 非 dict: {result!r}"
        # sidecar/生成软失败会用 {value:null, error}
        if result.get("value") is None and result.get("error"):
            print(f"[mattergen] ✗ 生成返回错误: {result['error']}")
            return 1
        structs = result.get("structures") or []
        assert structs, f"structures 为空: {result!r}"
        print(
            f"[mattergen] 生成 OK: num_generated={result.get('num_generated')}, "
            f"structures={len(structs)}, elapsed={done.get('elapsed_sec')}s"
        )
        s0 = structs[0]
        assert s0.get("formula"), f"首个结构缺 formula: {s0!r}"
        print(f"[mattergen]   · [0] formula={s0.get('formula')}, "
              f"has_cif={bool(s0.get('cif'))}")
    except AssertionError as e:
        print(f"[mattergen] ✗ 断言失败: {e}")
        return 1
    except MCPError as e:
        print(f"[mattergen] ✗ MCP 错误: {e}")
        return 1

    print("[mattergen] ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

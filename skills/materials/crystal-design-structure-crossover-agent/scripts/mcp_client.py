"""
MCP-over-HTTP 极简客户端（仅用 stdlib urllib）。

给 evolve_driver 用：把对 5 个已部署 MCP 的调用统一成 call_tool / call_async_tool。
各 MCP 用 FastMCP streamable-http + json_response=True，返回体是普通 JSON：
  {"jsonrpc":"2.0","id":N,"result":{"content":[{"type":"text","text":"<json 串>"}],"isError":bool}}
我们取 result.content[0].text 再 json.loads 得到工具真正的返回 dict。

异步工具统一两步：submit_tool(...) 立即回 {job_id}；poll_tool(job_id) 轮询，
done 时其 result 字段即真正结果。call_async_tool 封装"提交 + 轮询 + 取 result"。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

# 服务友好名 → 公网反代路径段（nginx strip /mcp/<segment> 后转发到容器内 /mcp）。
# 端点 = {MATERIALS_MCP_BASE_URL}/<segment>/mcp，base 必须由 .env 提供（不入库）。
_SERVICE_PATH = {
    "mutate": "diffusion-mutate",
    "relax": "mattersim-relax",
    "alignn": "alignn",
    "evaluate": "mattergen-evaluate",
    "generate": "mattergen-generate",
}

_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def get_endpoints() -> dict:
    """构造每个 MCP 的端点 URL。

    来源优先级（高→低）：
      1. 环境变量 MCP_<NAME>_URL —— 显式覆盖单个服务（例如同机 --network host 直连
         http://localhost:<port>/mcp）。
      2. {MATERIALS_MCP_BASE_URL}/<service>/mcp —— 公网反代基址，从 .env 读取。
         未设置时报错（IP/域名不入库）。
    """
    base = os.environ.get("MATERIALS_MCP_BASE_URL", "").rstrip("/")
    eps: dict = {}
    for name, segment in _SERVICE_PATH.items():
        explicit = os.environ.get(f"MCP_{name.upper()}_URL")
        if explicit:
            eps[name] = explicit
            continue
        if not base:
            raise RuntimeError(
                "MATERIALS_MCP_BASE_URL 未设置：请在仓库根 .env 中配置 "
                "MATERIALS_MCP_BASE_URL=http://<host>:<port>/mcp（与 skills/ 同级的 .env，"
                "run.sh 会自动加载），或为每个服务设置 MCP_<NAME>_URL。"
            )
        eps[name] = f"{base}/{segment}/mcp"
    return eps


def _post(endpoint: str, payload: dict, timeout: int = 120) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(endpoint, data=body, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _next_id(_cnt=[0]) -> int:
    _cnt[0] += 1
    return _cnt[0]


def call_tool(endpoint: str, name: str, arguments: dict, timeout: int = 120) -> dict:
    """调用同步 MCP 工具，返回其 JSON 结果 dict。

    失败语义：若 HTTP/JSON 层出错抛异常；工具内部失败由其自身返回 {value:null,error}。
    """
    payload = {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    resp = _post(endpoint, payload, timeout=timeout)
    result = resp.get("result", {})
    if result.get("isError"):
        raise RuntimeError(f"MCP tool '{name}' returned isError; raw: {result}")
    content = result.get("content") or []
    if not content:
        return result
    text = content[0].get("text", "{}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw_text": text}


def call_async_tool(
    endpoint: str,
    submit_name: str,
    poll_name: str,
    arguments: dict,
    *,
    total_wait: int = 600,
    interval: float = 5.0,
    submit_timeout: int = 60,
) -> dict:
    """提交异步工具并轮询至 done/error，返回其 result 字段（真正结果 dict）。

    submit 返回 {job_id,...}；poll 返回 {job_id, status, result?, ...}。
    """
    submit = call_tool(endpoint, submit_name, arguments, timeout=submit_timeout)
    job_id = submit.get("job_id")
    if not job_id:
        # submit 直接失败（如参数校验），原样返回错误信封
        return submit
    t0 = time.time()
    while time.time() - t0 < total_wait:
        st = call_tool(endpoint, poll_name, {"job_id": job_id}, timeout=120)
        status = st.get("status")
        if status in ("done", "error"):
            return st.get("result", st)
        time.sleep(interval)
    raise TimeoutError(f"async tool '{submit_name}' job {job_id} 未在 {total_wait}s 内完成")

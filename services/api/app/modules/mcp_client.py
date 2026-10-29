#!/usr/bin/env python3
"""
极简 MCP 客户端（仅依赖 Python 标准库）。

对着 FastMCP 的 streamable-http（stateless）端点说话：
  initialize → tools/list → tools/call

设计要点：
- stateless_http=True：无需 Mcp-Session-Id，每个请求独立。
- json_response=True 时返回 application/json；否则返回 text/event-stream。
  本客户端两种都能解析（SSE 取 data: 行里的 JSON-RPC 消息）。
- tools/call 的结果封装在 content[*].text 里（TextContent）；本客户端把
  各 text 拼接并尝试 json.loads —— 服务端工具统一返回 json.dumps 字符串。

不引入 requests / mcp SDK，便于在任意能访问到服务的主机上直接跑。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class MCPError(RuntimeError):
    """MCP 层（JSON-RPC error 或协议解析）失败。"""


class MCPClient:
    def __init__(self, base_url: str, timeout: float = 60.0, verbose: bool = True):
        # base_url 形如 http://localhost:9125/mcp
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self._id = 0

    # ---------- 低层 ----------
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, payload: dict[str, Any], parse: bool = True) -> dict[str, Any] | None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                # 同时声明接受两种响应，兼容 json_response 与 SSE
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise MCPError(f"HTTP {e.code} {e.reason} @ {self.base_url}: {detail[:500]}") from e
        except urllib.error.URLError as e:
            raise MCPError(f"无法连接 {self.base_url}: {e.reason}") from e

        if not parse:
            # 通知（notifications/*）通常返回空体或 202，无需解析
            return None
        return self._parse_response(body, ctype)

    def _parse_response(self, body: str, ctype: str) -> dict[str, Any]:
        if "text/event-stream" in ctype:
            # 收集所有 data: 行；取最后一条完整 JSON-RPC 消息（含 result/error）
            last_msg: dict[str, Any] | None = None
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    chunk = line[len("data:"):].strip()
                    if not chunk:
                        continue
                    try:
                        last_msg = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
            if last_msg is None:
                raise MCPError(f"SSE 响应无可解析 JSON: {body[:300]}")
            return last_msg
        # 普通 JSON
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise MCPError(f"JSON 解析失败: {e}; body[:300]={body[:300]!r}") from e

    # ---------- 协议方法 ----------
    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            payload["params"] = params
        if self.verbose:
            print(f"  → {method}")
        msg = self._post(payload)
        if "error" in msg:
            err = msg["error"]
            raise MCPError(f"{method} 返回错误: {json.dumps(err, ensure_ascii=False)}")
        if "result" not in msg:
            raise MCPError(f"{method} 响应无 result 字段: {msg!r}")
        return msg["result"]

    def initialize(self) -> dict[str, Any]:
        """MCP 握手。stateless 模式下也先发，确认服务在线与协议版本。"""
        result = self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "material-mcp-test", "version": "1.0"},
            },
        )
        # 规范要求握手后发 initialized 通知；stateless 多半不强制，但发了更稳。
        # 通知无 id、无响应体，parse=False 容错。
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, parse=False)
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._call("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """
        调用工具并返回解析后的 Python 对象。
        服务端工具统一 return json.dumps(...) 字符串，FastMCP 包成 TextContent，
        故这里把 content 里所有 text 拼起来再 json.loads。
        """
        result = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        content = result.get("content", []) if isinstance(result, dict) else []
        if result.get("isError"):
            text = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
            raise MCPError(f"工具 {name} 返回 isError=True: {text[:500]}")
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text  # 非 JSON 时原样返回文本

    def call_tool_async(
        self,
        submit_tool: str,
        submit_args: dict[str, Any],
        poll_tool: str,
        poll_arg_name: str = "job_id",
        interval: float = 5.0,
        timeout: float = 600.0,
    ) -> Any:
        """
        异步工具通用流程：submit 拿 job_id → 轮询 poll_tool 直到 done/error。

        submit 返回 {job_id, status:"pending", ...}；
        poll 返回 {status: pending/running/done/error, ...result}。
        """
        import time

        submit_res = self.call_tool(submit_tool, submit_args)
        if not isinstance(submit_res, dict) or "job_id" not in submit_res:
            raise MCPError(
                f"{submit_tool} 未返回 job_id: {submit_res!r}"
            )
        job_id = submit_res["job_id"]
        print(f"  · 提交成功 job_id={job_id}；轮询 {poll_tool}（每 {interval}s，上限 {timeout}s）")

        deadline = time.monotonic() + timeout
        last_status = None
        while time.monotonic() < deadline:
            res = self.call_tool(poll_tool, {poll_arg_name: job_id})
            if not isinstance(res, dict):
                raise MCPError(f"{poll_tool} 返回非 dict: {res!r}")
            status = res.get("status")
            if status != last_status:
                print(f"  · status={status}")
                last_status = status
            if status == "done":
                return res
            if status == "error":
                raise MCPError(f"任务 {job_id} 失败: {res.get('error') or res}")
            time.sleep(interval)
        raise MCPError(f"任务 {job_id} 轮询超时（{timeout}s）")


def env_url(default: str, *env_keys: str) -> str:
    """从多个候选环境变量取服务 URL，取不到用 default。"""
    import os

    for k in env_keys:
        v = os.environ.get(k)
        if v:
            return v.rstrip("/")
    return default.rstrip("/")


def cif_content_from(env_var: str, argv_flag: str = "--cif", fallback: str | None = None) -> str | None:
    """
    解析用户提供的 CIF 文件路径，读取并返回其正文（供 cif_content 参数发送）。

    优先级：命令行 `--cif PATH` > 环境变量 env_var > fallback（内置示例 CIF）。
    远程服务看不到本地路径，故这里读到正文后以 cif_content 形式发送。
    """
    import os
    import sys

    path = None
    if argv_flag in sys.argv:
        i = sys.argv.index(argv_flag)
        if i + 1 < len(sys.argv):
            path = sys.argv[i + 1]
    if not path:
        path = os.environ.get(env_var)
    if not path:
        return fallback
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def ensure_self_on_path() -> None:
    """把本脚本所在目录加到 sys.path 头部，使 `python tests/test_x.py` 在任意 cwd 下都能 import mcp_client。"""
    import os
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


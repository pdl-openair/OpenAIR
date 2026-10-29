"""本地开发启动入口。

Windows 下 uvicorn --reload 会启用 SelectorEventLoop，导致 Claude SDK 子进程启动失败。
因此默认关闭 reload，可通过 OPENAIR_API_RELOAD=1 显式开启。
"""

from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _check_port_available(host: str, port: int) -> None:
    """启动前检测端口是否被占用。Windows 允许 0.0.0.0 和 127.0.0.1 同时绑定同一端口，
    因此同时检测两个接口，避免静默共存导致请求被抢。"""
    import socket

    for probe_host in ("0.0.0.0", "127.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # SO_REUSEADDR: 仅允许在 TIME-WAIT 残留上绑定，
                # 真正的 LISTEN 占用仍会正确报错。
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.settimeout(1)
                s.bind((probe_host, port))
        except OSError:
            print(
                f"[openair] error: 端口 {port} 已被占用（{probe_host}），"
                "请先关闭占用进程或换端口。\n"
                f"  排查命令: ss -tlnp | grep :{port}    (WSL/Linux)\n"
                f"           netstat -ano | findstr :{port}    (Windows)",
                file=sys.stderr,
            )
            sys.exit(1)


if __name__ == "__main__":
    import uvicorn

    _check_port_available("127.0.0.1", 8000)

    reload_enabled = _env_truthy("OPENAIR_API_RELOAD", default=(sys.platform != "win32"))
    if sys.platform == "win32" and reload_enabled:
        print("[openair] warn: Windows + reload 可能导致 Claude Code 子进程失败，请确认后再使用。")

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=reload_enabled,
        # Long-lived SSE connections must not keep development reload waiting forever.
        timeout_graceful_shutdown=10,
    )

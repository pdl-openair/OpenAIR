"""Resolve Claude Code CLI executable (Windows PATH 与 IDE 启动后端时不一致)."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHED_CLI_PATH: str | None = None


def _windows_local_bin_candidates() -> list[Path]:
    base = Path.home() / ".local" / "bin"
    return [
        base / "claude.exe",
        base / "claude.cmd",
        base / "claude",
    ]


def resolve_claude_cli_path() -> str:
    """
    解析 claude 可执行文件路径。
    优先级：OPENAIR_CLAUDE_CLI_PATH > Windows ~/.local/bin > PATH > 其他常见位置。
    """
    global _CACHED_CLI_PATH
    if _CACHED_CLI_PATH:
        return _CACHED_CLI_PATH

    override = (os.environ.get("OPENAIR_CLAUDE_CLI_PATH") or "").strip()
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            _CACHED_CLI_PATH = str(p.resolve())
            return _CACHED_CLI_PATH
        raise FileNotFoundError(f"OPENAIR_CLAUDE_CLI_PATH not found: {override}")

    # Windows：优先固定安装位（IDE 启动的 uvicorn 常无 ~/.local/bin 在 PATH）
    if os.name == "nt":
        for c in _windows_local_bin_candidates():
            if c.is_file():
                _CACHED_CLI_PATH = str(c.resolve())
                logger.info("claude cli resolved (windows): %s", _CACHED_CLI_PATH)
                return _CACHED_CLI_PATH

    found = shutil.which("claude")
    if found:
        p = Path(found)
        if p.is_file():
            _CACHED_CLI_PATH = str(p.resolve())
            return _CACHED_CLI_PATH

    if os.name == "nt":
        extra = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe"
        if extra.is_file():
            _CACHED_CLI_PATH = str(extra.resolve())
            return _CACHED_CLI_PATH

    raise FileNotFoundError(
        "未找到 Claude Code CLI。请设置 OPENAIR_CLAUDE_CLI_PATH="
        r"C:\Users\<你>\.local\bin\claude.exe（本机 which claude 常见路径）"
    )


def cli_env_with_path(cli_path: str) -> dict[str, str]:
    """确保子进程 PATH 包含 claude 所在目录。"""
    env = dict(os.environ)
    cli_dir = str(Path(cli_path).parent)
    path_key = "PATH"
    current = env.get(path_key, "")
    parts = current.split(os.pathsep) if current else []
    if cli_dir not in parts:
        env[path_key] = os.pathsep.join([cli_dir, *parts])
    # 部分 CLI 依赖 USERPROFILE / HOME
    if os.name == "nt" and "USERPROFILE" not in env:
        env["USERPROFILE"] = str(Path.home())
    return env

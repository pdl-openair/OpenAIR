#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""openair —— 一条命令拉起 OpenAIR 科研智能体平台。

用法（发布后）：
    pipx install openair-cli      # 或 pip install --user openair-cli
    openair up                    # 克隆/更新仓库 → 准备 .env → 启动全栈 → 打开浏览器
    openair status                # 查看服务与健康状态
    openair seed                  # 灌入演示数据（replay 演示推荐）
    openair down                  # 停止全栈（--volumes 连数据卷一起删）

本地开发安装：
    uv tool install --force ./cli     # 仓库根目录执行
"""

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

REPO_URL = "https://github.com/pdl-openair/OpenAIR.git"
API_HEALTH = "http://127.0.0.1:8000/health"
DEFAULT_WEB_PORT = "8080"
HEALTH_TIMEOUT_SECONDS = 600

# .env 首次生成时随机化的安全项（覆盖 .env.example 的本地默认值）
RANDOM_SECRETS = {
    "POSTGRES_PASSWORD": lambda: secrets.token_hex(16),
    "OPENAIR_MINIO_ROOT_PASSWORD": lambda: secrets.token_hex(16),
    "OPENAIR_JWT_SECRET_KEY": lambda: secrets.token_hex(32),
    "OPENAIR_WORKER_AUTH_TOKEN": lambda: secrets.token_hex(16),
}


def say(msg):
    print(msg, flush=True)


def die(msg, code=1):
    print(f"[openair] 错误: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def sh(cmd, cwd=None, check=True):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        die(f"命令失败: {' '.join(cmd)}\n{proc.stderr.strip()[:800]}")
    return proc


def compose_cmd():
    if sh(["docker", "compose", "version"], check=False).returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    die("未找到 Docker / Docker Compose。请先安装 Docker 24+（https://docs.docker.com/get-docker/），"
        "国内网络可参考仓库 README 的镜像加速配置")


def repo_dir(args) -> Path:
    d = Path(args.dir or "OpenAIR").resolve()
    if d.exists() and not d.is_dir():
        die(f"路径不是目录: {d}")
    return d


def ensure_repo(d: Path) -> bool:
    """存在则更新（容忍失败），不存在则克隆。返回是否为首次。"""
    if (d / ".git").exists():
        say(f"· 更新仓库 {d}")
        sh(["git", "pull", "--ff-only"], cwd=str(d), check=False)
        return False
    if d.exists() and any(d.iterdir()):
        die(f"目录非空且不是仓库: {d}（换 --dir 指定其他位置）")
    say(f"· 克隆仓库 -> {d}")
    sh(["git", "clone", "--depth", "1", REPO_URL, str(d)])
    return True


def env_path(d: Path) -> Path:
    return d / ".env"


def read_env(p: Path):
    return p.read_text(encoding="utf-8").splitlines()


def set_env(d: Path, key: str, value: str):
    """写入/替换 .env 中的 key=value（支持替换被注释的 `# key=` 行）。"""
    p = env_path(d)
    lines = read_env(p) if p.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def get_env(d: Path, key: str, default: str = "") -> str:
    p = env_path(d)
    if not p.exists():
        return default
    for line in read_env(p):
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def prepare_env(d: Path, args) -> str:
    """首次生成 .env（随机安全项 + bootstrap 管理员）；返回管理员密码（仅首次打印）。"""
    p = env_path(d)
    if p.exists():
        return ""
    say("· 首次生成 .env（安全项已随机化）")
    admin_email = args.admin_email or "admin@openair.local"
    admin_password = args.admin_password or secrets.token_urlsafe(12)
    lines = read_env(d / ".env.example")
    out = []
    for line in lines:
        replaced = False
        for key, gen in RANDOM_SECRETS.items():
            if line.startswith(f"{key}="):
                out.append(f"{key}={gen()}")
                replaced = True
                break
        if not replaced:
            out.append(line)
    out += [
        "",
        "# ===== openair-cli 生成的管理员（compose 强制登录，用它在工作台登录）=====",
        f"OPENAIR_BOOTSTRAP_ADMIN_EMAIL={admin_email}",
        f"OPENAIR_BOOTSTRAP_ADMIN_PASSWORD={admin_password}",
        "OPENAIR_BOOTSTRAP_ADMIN_USERNAME=ops",
    ]
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return admin_password


def wait_healthy(timeout=HEALTH_TIMEOUT_SECONDS):
    say("· 等待 API 就绪（首次构建镜像可能需要几分钟）")
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(API_HEALTH, timeout=3) as resp:
                if resp.status == 200:
                    say("  API 健康: ok")
                    return True
        except Exception:
            pass
        time.sleep(3)
        if int(time.time() - start) % 15 == 0:
            say(f"  仍在等待… {int(time.time() - start)}s（可用 `docker compose logs api` 排障）")
    return False


def cmd_up(args):
    d = repo_dir(args)
    ensure_repo(d)
    admin_pw = prepare_env(d, args)
    if args.mode:
        set_env(d, "SKILL_EXEC_MODE", args.mode)
    if args.seed:
        set_env(d, "OPENAIR_DEV_SEED_FIXTURES", "1")
    if admin_pw:
        say("\n┌────────────── 登录账号（仅此一次展示，已写入 .env）──────────────")
        say(f"│  邮箱: {(args.admin_email or 'admin@openair.local')}")
        say(f"│  密码: {admin_pw}")
        say(f"│  用户名: ops（.env 中 OPENAIR_BOOTSTRAP_ADMIN_* 可改）")
        say("└──────────────────────────────────────────────────────────\n")
    if args.no_start:
        say(f"· --no-start：已准备好仓库与 .env，未启动。目录: {d}")
        return
    cc = compose_cmd()
    mode = get_env(d, "SKILL_EXEC_MODE", "live")
    say(f"· 启动全栈（SKILL_EXEC_MODE={mode}）")
    sh(cc + ["up", "-d", "--build"], cwd=str(d))
    if not wait_healthy():
        die("API 未在超时内就绪。请 `docker compose logs api` 查看日志后重试 `openair up`")
    if mode == "live":
        say("· 提示: 当前 live 模式，需在 .env 配置模型网关（ANTHROPIC_* / LLM_PROVIDER_*）；"
            "零密钥体验可 `openair up --mode replay`")
    if not args.no_open:
        port = get_env(d, "OPENAIR_WEB_PORT", DEFAULT_WEB_PORT)
        url = f"http://localhost:{port}"
        say(f"· 打开工作台 {url}")
        webbrowser.open(url)
    say("完成。停止用 `openair down`，演示数据用 `openair seed`。")


def cmd_down(args):
    d = repo_dir(args)
    if not (d / "docker-compose.yml").exists():
        die(f"目录下没有 docker-compose.yml: {d}")
    cc = compose_cmd()
    say(f"· 停止全栈{('（含数据卷）' if args.volumes else '（数据保留）')}")
    sh(cc + (["down", "-v"] if args.volumes else ["down"]), cwd=str(d))


def cmd_seed(args):
    d = repo_dir(args)
    if not env_path(d).exists():
        die("尚未初始化（先 `openair up`）")
    set_env(d, "OPENAIR_DEV_SEED_FIXTURES", "1")
    cc = compose_cmd()
    say("· 重建 api 服务以灌入演示数据")
    sh(cc + ["up", "-d", "api"], cwd=str(d))
    say("完成。工作台将出现演示任务（replay 模式下执行零密钥）")


def cmd_status(args):
    d = repo_dir(args)
    cc = compose_cmd()
    if (d / "docker-compose.yml").exists():
        sh(cc + ["ps"], cwd=str(d))
    try:
        with urllib.request.urlopen(API_HEALTH, timeout=3) as resp:
            say(f"API 健康: {'ok' if resp.status == 200 else resp.status}")
    except Exception:
        say("API: 未响应（未启动或端口未开放）")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="openair",
        description="OpenAIR 科研智能体平台 · 一键启动器（up/status/seed/down）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("up", help="克隆/更新仓库 → 准备 .env → 启动全栈 → 打开浏览器")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.add_argument("--mode", choices=["live", "replay"], help="执行模式（写入 .env）")
    p.add_argument("--seed", action="store_true", help="启动时灌入演示数据")
    p.add_argument("--admin-email", help="首次生成 .env 时的管理员邮箱")
    p.add_argument("--admin-password", help="首次生成 .env 时的管理员密码（缺省自动生成）")
    p.add_argument("--no-open", action="store_true", help="启动后不打开浏览器")
    p.add_argument("--no-start", action="store_true", help="只准备仓库与 .env，不启动容器")
    p.set_defaults(fn=cmd_up)

    p = sub.add_parser("down", help="停止全栈")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.add_argument("--volumes", action="store_true", help="连同数据卷删除（清空所有数据）")
    p.set_defaults(fn=cmd_down)

    p = sub.add_parser("seed", help="灌入演示数据")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("status", help="查看服务与 API 健康状态")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

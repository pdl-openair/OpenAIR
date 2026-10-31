#!/usr/bin/env python3
"""openair —— 一条命令拉起 OpenAIR 科研智能体平台。

用法（发布后）：
    pipx install openair-cli      # 或 pip install --user openair-cli
    openair up                    # 体检 → 克隆/更新仓库 → 环境配置引导 → 启动全栈 → 打开浏览器
    openair config                # 交互式重配 .env（执行模式 / 模型网关 / 可选密钥）
    openair status                # 查看服务与健康状态
    openair logs [服务]           # 跟踪容器日志（默认跟随，--no-follow 只看尾部）
    openair seed                  # 灌入演示数据（replay 演示推荐）
    openair down                  # 停止全栈（--volumes 连数据卷一起删）
    openair doctor                # 环境体检（Docker / compose / 端口 / 磁盘 / 镜像加速）

本地开发安装：
    uv tool install --force ./cli     # 仓库根目录执行
"""

import argparse
import getpass
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path

__version__ = "0.2.0"

REPO_URL = "https://github.com/pdl-openair/OpenAIR.git"
API_HEALTH = "http://127.0.0.1:8000/health"
API_PORT = 8000
DEFAULT_WEB_PORT = "8080"
HEALTH_TIMEOUT_SECONDS = 600
MIN_FREE_GB = 20  # 镜像构建 + 运行的建议磁盘下限
DOCKER_INSTALL_URL = "https://docs.docker.com/get-docker/"
MP_APPLY_URL = "https://next-gen.materialsproject.org/api"

# 国内网络拉镜像失败的缓解配置（与公开仓 README「国内网络环境配置」一致；地址时效性强）
MIRROR_HINT_JSON = (
    '{\n  "registry-mirrors": [\n'
    '    "https://docker.1panel.live",\n'
    '    "https://hub.rat.dev",\n'
    '    "https://docker.mirrors.ustc.edu.cn",\n'
    '    "https://hub-mirror.c.163.com",\n'
    '    "https://mirror.baidubce.com"\n'
    '  ]\n}'
)

# .env 首次生成时随机化的安全项（覆盖 .env.example 的本地默认值）
RANDOM_SECRETS = {
    "POSTGRES_PASSWORD": lambda: secrets.token_hex(16),
    "OPENAIR_MINIO_ROOT_PASSWORD": lambda: secrets.token_hex(16),
    "OPENAIR_JWT_SECRET_KEY": lambda: secrets.token_hex(32),
    "OPENAIR_WORKER_AUTH_TOKEN": lambda: secrets.token_hex(16),
}

# 命中即给出「镜像拉取失败 → 镜像加速」引导的典型关键词
_PULL_TROUBLE_KEYS = (
    "i/o timeout", "tls handshake", "connection refused", "context deadline exceeded",
    "toomanyrequests", "manifest unknown", "unexpected eof", "net/http: request canceled",
)


def say(msg):
    print(msg, flush=True)


def note(msg):
    say(f"  · {msg}")


def die(msg, code=1):
    print(f"[openair] 错误: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def step(i, n, msg):
    say(f"\n──── [{i}/{n}] {msg}")


def sh(cmd, cwd=None, check=True):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        die(f"命令失败: {' '.join(cmd)}\n{(proc.stderr or '').strip()[:800]}")
    return proc


def run_live(cmd, cwd=None, env_extra=None, tail_keep=80):
    """实时透传子进程输出（构建/拉取进度可见），同时保留尾部日志用于失败诊断。"""
    env = dict(os.environ) if env_extra else None
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    tail = deque(maxlen=tail_keep)
    for line in proc.stdout:
        line = line.rstrip("\n")
        tail.append(line)
        say(f"  {line}" if line else "")
    code = proc.wait()
    return code, list(tail)


def run_inherit(cmd, cwd=None):
    """直接继承终端运行（git/docker 自带的进度条可用）。返回退出码。"""
    proc = subprocess.run(cmd, cwd=cwd)
    return proc.returncode


# ---------------------------------------------------------------- Docker 检查

def find_compose():
    if sh(["docker", "compose", "version"], check=False).returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def compose_cmd():
    cc = find_compose()
    if cc is None:
        die("未找到 Docker Compose v2（或 docker-compose）。请先安装 Docker 24+：\n"
            f"  {DOCKER_INSTALL_URL}\n"
            "  国内网络可先 `openair doctor` 体检；镜像加速配置见仓库 README「国内网络环境配置」")
    return cc


def docker_preflight():
    """启动前快速体检：docker 存在、版本、守护进程、compose。失败即 die。"""
    if not shutil.which("docker"):
        die("未找到 docker。请先安装 Docker 24+：\n"
            f"  {DOCKER_INSTALL_URL}\n"
            "  装好后可用 `openair doctor` 逐项体检")
    ver = sh(["docker", "--version"], check=False).stdout or ""
    m = re.search(r"version (\d+)\.", ver)
    if m and int(m.group(1)) < 24:
        note(f"警告: {ver.strip()} 版本偏低，建议 24+")
    info = sh(["docker", "info"], check=False)
    if info.returncode != 0:
        die("Docker 守护进程未运行。\n"
            "  Windows: 启动 Docker Desktop；Linux: systemctl start docker\n"
            "  就绪后重试 `openair up`，或先 `openair doctor` 体检")
    compose_cmd()  # 顺手确认 compose 可用


# ---------------------------------------------------------------- 仓库与 .env

def repo_dir(args) -> Path:
    d = Path(args.dir or "OpenAIR").resolve()
    if d.exists() and not d.is_dir():
        die(f"路径不是目录: {d}")
    return d


def ensure_repo(d: Path) -> bool:
    """存在则更新（容忍失败），不存在则克隆。返回是否为首次。"""
    if (d / ".git").exists():
        say(f"· 更新仓库 {d}")
        if run_inherit(["git", "pull", "--ff-only"], cwd=str(d)) != 0:
            note("更新失败（本地有改动或网络问题），继续用当前版本")
        return False
    if d.exists() and any(d.iterdir()):
        die(f"目录非空且不是仓库: {d}（换 --dir 指定其他位置）")
    say(f"· 克隆仓库（浅克隆，进度见下） -> {d}")
    if run_inherit(["git", "clone", "--depth", "1", "--progress", REPO_URL, str(d)]) != 0:
        die("克隆失败：请检查网络后重试；国内网络访问 GitHub 慢可先配置代理")
    return True


def env_path(d: Path) -> Path:
    return d / ".env"


def read_env(p: Path):
    return p.read_text(encoding="utf-8").splitlines()


def set_env(d: Path, key: str, value: str):
    """写入/替换 .env 中的 key=value（替换首处，含被注释的 `# key=` 形态；去重）。"""
    p = env_path(d)
    lines = read_env(p) if p.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            # 后续重复项（含注释形态）丢弃，保证 key 只有一份生效行
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
        # 已有 .env 也允许用参数覆盖管理员（改完需重启容器生效）
        if args.admin_email:
            set_env(d, "OPENAIR_BOOTSTRAP_ADMIN_EMAIL", args.admin_email)
            say("· 已按 --admin-email 更新管理员邮箱（重启后生效）")
        if args.admin_password:
            set_env(d, "OPENAIR_BOOTSTRAP_ADMIN_PASSWORD", args.admin_password)
            say("· 已按 --admin-password 更新管理员密码（重启后生效）")
        return ""
    example = d / ".env.example"
    if not example.exists():
        die(f"仓库里没有 .env.example（{example}），仓库不完整？")
    say("· 首次生成 .env（安全项已随机化）")
    admin_email = args.admin_email or "admin@openair.local"
    admin_password = args.admin_password or secrets.token_urlsafe(12)
    out = []
    for line in read_env(example):
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


# ---------------------------------------------------------------- 环境配置引导

def is_interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask(prompt, default="", secret=False):
    """交互提问，回车取默认值；secret=True 时输入不回显（适合密钥）。"""
    shown = f"{prompt}［{default}］" if (default and not secret) else prompt
    shown += ": "
    if secret:
        try:
            val = getpass.getpass(shown)
        except Exception:  # noqa: BLE001 — 管道/无终端环境下降级为默认值
            return default
    else:
        try:
            val = input(shown)
        except EOFError:
            return default
    val = val.strip()
    return val if val else default


def ask_choice(prompt, options, default_idx=0):
    """编号单选。options: [(value, 说明)]，返回 value。"""
    say(f"\n{prompt}")
    for i, (_, label) in enumerate(options, 1):
        say(f"  {i}) {label}")
    default_no = str(default_idx + 1)
    while True:
        try:
            raw = input(f"选择 [1-{len(options)}]（回车={default_no}）: ").strip()
        except EOFError:
            return options[default_idx][0]
        if not raw:
            return options[default_idx][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        say("  无效输入，请输入编号")


def mask(v):
    if not v:
        return "（未配置）"
    return f"{v[:4]}…{v[-2:]}" if len(v) > 8 else "***"


def config_wizard(d: Path):
    """环境配置引导（对应公开仓 README「按需配置分组」），实时写入 .env。"""
    cur_mode = get_env(d, "SKILL_EXEC_MODE", "live")
    mode = ask_choice(
        "① 执行模式 SKILL_EXEC_MODE —— 决定 skill 执行走真实模型还是零密钥回放",
        [("replay", "replay —— 零密钥回放：不需要任何 API key，"
                    "用仓库内置 fixtures 跑通全流程（推荐第一次）"),
         ("live", "live —— 真实执行：skill 调用 Anthropic 兼容网关，"
                  "开题规划走 OpenAI 兼容端点（需下方密钥）")],
        default_idx=0 if cur_mode != "live" else 1)
    set_env(d, "SKILL_EXEC_MODE", mode)
    say(f"  → SKILL_EXEC_MODE={mode}")
    if mode == "replay":
        note("replay 无需任何密钥。之后想接真实网关，随时 `openair config` 重跑本向导")
        return

    say("\n② Skill/Agent 网关（Anthropic 兼容；live 模式必填）")
    v = ask("   ANTHROPIC_BASE_URL（网关地址）", default=get_env(d, "ANTHROPIC_BASE_URL"))
    set_env(d, "ANTHROPIC_BASE_URL", v)
    say(f"  → ANTHROPIC_BASE_URL={v or '（空）'}")
    k = ask("   ANTHROPIC_API_KEY（密钥，回车=保持现值）", secret=True)
    if k:
        set_env(d, "ANTHROPIC_API_KEY", k)
    say(f"  → ANTHROPIC_API_KEY={mask(k) if k else '（保持现值）'}")
    m = ask("   OPENAIR_CLAUDE_MODEL（执行模型名）",
            default=get_env(d, "OPENAIR_CLAUDE_MODEL", "claude-sonnet-4-5"))
    set_env(d, "OPENAIR_CLAUDE_MODEL", m)
    say(f"  → OPENAIR_CLAUDE_MODEL={m}")

    say("\n③ 开题规划器（OpenAI 兼容端点；live 模式必填）")
    note("不要填 ANTHROPIC 那种 …/api/anthropic 路径，规划请求会 404")
    v = ask("   LLM_PROVIDER_BASE_URL（OpenAI 兼容地址）",
            default=get_env(d, "LLM_PROVIDER_BASE_URL"))
    set_env(d, "LLM_PROVIDER_BASE_URL", v)
    say(f"  → LLM_PROVIDER_BASE_URL={v or '（空）'}")
    k = ask("   LLM_PROVIDER_API_KEY（密钥，回车=保持现值）", secret=True)
    if k:
        set_env(d, "LLM_PROVIDER_API_KEY", k)
    say(f"  → LLM_PROVIDER_API_KEY={mask(k) if k else '（保持现值）'}")
    m = ask("   LLM_PROVIDER_MODEL（规划模型名）",
            default=get_env(d, "LLM_PROVIDER_MODEL", "gpt-5.6-terra"))
    set_env(d, "LLM_PROVIDER_MODEL", m)
    say(f"  → LLM_PROVIDER_MODEL={m}")

    say(f"\n④ 可选外部账号（都可回车跳过；Materials Project 免费 key：{MP_APPLY_URL}）")
    k = ask("   MP_API_KEY（材料候选检索用）", secret=True)
    if k:
        set_env(d, "MP_API_KEY", k)
        say(f"  → MP_API_KEY={mask(k)}")
    note("其余可选项（文献检索 / 材料 MCP / 超算 JCC / 云实验 FLUX）见 .env.example 分组注释")


# ---------------------------------------------------------------- 启动与等待

def hint_on_failure(tail_lines):
    t = "\n".join(tail_lines).lower()
    hinted = False
    if any(k in t for k in _PULL_TROUBLE_KEYS):
        say("")
        say("· 疑似镜像拉取失败（国内网络常见）。缓解方式：")
        say("  1) 给 Docker 配置镜像加速（Docker Desktop → Settings → Docker Engine，加入）：")
        for line in MIRROR_HINT_JSON.splitlines():
            say(f"     {line}")
        say("     Apply & Restart 后重试 `openair up`（加速地址时效性强，"
            "全失效就搜索「docker 镜像加速 可用」换新）")
        hinted = True
    if "port is already allocated" in t or "address already in use" in t:
        say("· 端口被占用：`openair status` 看平台是否已在运行；"
            "或改 .env 的 OPENAIR_WEB_PORT / api 端口")
        hinted = True
    if "no space left on device" in t or "out of space" in t:
        say("· 磁盘空间不足：`docker system prune` 清理后重试（`openair doctor` 可看磁盘余量）")
        hinted = True
    if not hinted:
        say("· 未见典型网络/端口/磁盘故障；`openair doctor` 逐项体检，"
            "`openair logs api` 看服务日志")


def api_healthy():
    try:
        with urllib.request.urlopen(API_HEALTH, timeout=3) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 — 未就绪时的正常轮询路径
        return False


def wait_healthy(timeout=HEALTH_TIMEOUT_SECONDS):
    say("· 等待 API 就绪（首次启动要初始化数据库，通常 1-3 分钟）")
    start = time.time()
    is_tty = sys.stdout.isatty()
    last_note = 0
    while True:
        elapsed = int(time.time() - start)
        if elapsed >= timeout:
            return False
        if api_healthy():
            if is_tty:
                sys.stdout.write("\r" + " " * 72 + "\r")
                sys.stdout.flush()
            say("  API 健康: ok")
            return True
        if is_tty:
            sys.stdout.write(f"\r  已等待 {elapsed}s …")
            sys.stdout.flush()
        elif elapsed - last_note >= 15:
            say(f"  仍在等待… {elapsed}s（可另开终端 `openair logs api` 排障）")
            last_note = elapsed
        time.sleep(2)


# ---------------------------------------------------------------- 子命令

def cmd_up(args):
    total = 6
    step(1, total, "环境体检（docker / 守护进程 / compose）")
    docker_preflight()

    d = repo_dir(args)
    step(2, total, f"获取仓库（{d}）")
    ensure_repo(d)

    step(3, total, "生成 .env 并做环境配置引导")
    admin_pw = prepare_env(d, args)
    if args.mode:
        set_env(d, "SKILL_EXEC_MODE", args.mode)
    if args.seed:
        set_env(d, "OPENAIR_DEV_SEED_FIXTURES", "1")
    if is_interactive() and not args.yes:
        config_wizard(d)
    elif not args.yes:
        note("非交互终端，跳过配置向导；需要时运行 `openair config`（或直接编辑 .env）")

    if admin_pw:
        say("")
        say("┌────────────── 登录账号（仅此一次展示，已写入 .env）──────────────")
        say(f"│  邮箱: {(args.admin_email or 'admin@openair.local')}")
        say(f"│  密码: {admin_pw}")
        say("│  用户名: ops（.env 中 OPENAIR_BOOTSTRAP_ADMIN_* 可改）")
        say("└──────────────────────────────────────────────────────────")

    if args.no_start:
        say(f"\n· --no-start：仓库与 .env 已就绪，未启动。目录: {d}")
        return

    cc = compose_cmd()
    mode = get_env(d, "SKILL_EXEC_MODE", "live")
    step(4, total, f"构建并启动容器（SKILL_EXEC_MODE={mode}；首次构建约 3-10 分钟，进度实时输出）")
    code, tail = run_live(cc + ["up", "-d", "--build"], cwd=str(d),
                          env_extra={"BUILDKIT_PROGRESS": "plain"})
    if code != 0:
        say("· 构建启动失败，最近日志：")
        for line in tail[-25:]:
            say(f"  {line}")
        hint_on_failure(tail)
        die("请按上面提示处理后重试 `openair up`")

    step(5, total, "等待 API 就绪")
    if not wait_healthy():
        die("API 未在超时内就绪。`openair logs api` 查看日志后重试 `openair up`")
    if mode == "live":
        missing = [k for k in ("ANTHROPIC_API_KEY", "LLM_PROVIDER_API_KEY") if not get_env(d, k)]
        if missing:
            note(f"live 模式但未配置 {', '.join(missing)}：涉及模型调用的节点会失败")
            note("零密钥体验改用 `openair up --mode replay`；配密钥用 `openair config`")

    step(6, total, "打开工作台")
    port = get_env(d, "OPENAIR_WEB_PORT", DEFAULT_WEB_PORT)
    url = f"http://localhost:{port}"
    say(f"· {url}")
    if not args.no_open:
        webbrowser.open(url)
    say("\n完成。常用后续：openair status / logs api / seed / down；重配环境 openair config")


def cmd_config(args):
    d = repo_dir(args)
    if not env_path(d).exists():
        die("尚未初始化 .env（先 `openair up`，或用 --dir 指向已有部署目录）")
    if not is_interactive():
        die("openair config 需要交互式终端；也可直接编辑 .env 后 `docker compose up -d` 生效")
    config_wizard(d)
    say("\n已写入 .env。应用改动：`openair up`（会重建受影响容器），或 `docker compose up -d`")


def cmd_status(args):
    d = repo_dir(args)
    if (d / "docker-compose.yml").exists():
        cc = compose_cmd()
        say("── 容器状态 ──")
        run_inherit(cc + ["ps"], cwd=str(d))
    else:
        say(f"（目录下没有 docker-compose.yml: {d}，先 `openair up`）")
    say("── 健康检查 ──")
    say(f"API: {'ok' if api_healthy() else '未响应（未启动或端口未开放）'}")
    port = get_env(d, "OPENAIR_WEB_PORT", DEFAULT_WEB_PORT)
    say(f"工作台: http://localhost:{port}")


def cmd_logs(args):
    d = repo_dir(args)
    if not (d / "docker-compose.yml").exists():
        die(f"目录下没有 docker-compose.yml: {d}")
    cc = compose_cmd()
    cmd = cc + ["logs", f"--tail={args.tail}"]
    if not args.no_follow:
        cmd.append("-f")
    if args.service:
        cmd.append(args.service)
    sys.exit(run_inherit(cmd, cwd=str(d)))


def cmd_seed(args):
    d = repo_dir(args)
    if not env_path(d).exists():
        die("尚未初始化（先 `openair up`）")
    set_env(d, "OPENAIR_DEV_SEED_FIXTURES", "1")
    cc = compose_cmd()
    say("· 重建 api 服务以灌入演示数据")
    if run_inherit(cc + ["up", "-d", "api"], cwd=str(d)) != 0:
        die("灌入失败：`openair logs api` 查看日志")
    say("完成。工作台将出现演示任务（replay 模式下执行零密钥）")


def cmd_down(args):
    d = repo_dir(args)
    if not (d / "docker-compose.yml").exists():
        die(f"目录下没有 docker-compose.yml: {d}")
    cc = compose_cmd()
    scope = "（含数据卷，数据将清空）" if args.volumes else "（数据保留，下次 up 恢复）"
    say(f"· 停止全栈{scope}")
    if run_inherit(cc + (["down", "-v"] if args.volumes else ["down"]), cwd=str(d)) != 0:
        die("停止失败：`openair status` 查看当前状态")


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def cmd_doctor(args):
    ok = True

    def result(good, msg):
        nonlocal ok
        ok = ok and good
        say(f"{'✓' if good else '✗'} {msg}")

    say(f"openair-cli {__version__} · 环境体检")
    say("── 基础依赖 ──")
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    result(sys.version_info >= (3, 9), f"Python {py}（需 ≥3.9）")
    if shutil.which("git"):
        result(True, (sh(["git", "--version"], check=False).stdout or "").strip())
    else:
        result(False, "未找到 git（克隆仓库需要；https://git-scm.com/downloads）")
    has_docker = bool(shutil.which("docker"))
    result(has_docker, "docker 已安装" if has_docker
           else f"未找到 docker（需 24+：{DOCKER_INSTALL_URL}）")

    say("── Docker 运行时 ──")
    if not has_docker:
        result(False, "跳过（未安装 docker）")
        info_proc = None
        cc = None
    else:
        ver = (sh(["docker", "--version"], check=False).stdout or "").strip()
        m = re.search(r"version (\d+)\.", ver)
        result(bool(m), f"{ver}（版本号无法解析）" if not m
               else (f"{ver}（需 24+）" if int(m.group(1)) < 24 else ver))
        info_proc = sh(["docker", "info"], check=False)
        result(info_proc.returncode == 0, "Docker 守护进程运行中" if info_proc.returncode == 0
               else "守护进程未运行（Windows: 启动 Docker Desktop；Linux: systemctl start docker）")
        cc = find_compose()
        compose_msg = "Compose: " + " ".join(cc) if cc else "未找到 Docker Compose v2"
        result(cc is not None, compose_msg)
        if info_proc.returncode == 0:
            mirrors = "Registry Mirrors:" in (info_proc.stdout or "")
            mirror_msg = ("已配置镜像加速" if mirrors
                          else "未配置镜像加速（国内网络建议配置，见 README「国内网络环境配置」）")
            say(f"{'✓' if mirrors else 'ℹ'} {mirror_msg}")

    say("── 端口与磁盘 ──")
    web_port = int(get_env(repo_dir(args), "OPENAIR_WEB_PORT", DEFAULT_WEB_PORT))
    for name, port in (("API", API_PORT), ("工作台", web_port)):
        if port_in_use(port):
            if port == API_PORT and api_healthy():
                say(f"ℹ 端口 {port}（{name}）被占用 —— 平台 API 正在运行（openair status）")
            else:
                result(False, f"端口 {port}（{name}）被其他进程占用"
                               "（改 OPENAIR_WEB_PORT 或释放端口）")
        else:
            say(f"✓ 端口 {port}（{name}）可用")
    usage = shutil.disk_usage(Path.cwd())
    free_gb = usage.free / (1 << 30)
    say(f"{'✓' if free_gb >= MIN_FREE_GB else '✗'} 磁盘剩余 {free_gb:.0f} GB"
        + ("" if free_gb >= MIN_FREE_GB else f"（低于 {MIN_FREE_GB} GB，构建镜像可能失败）"))

    say("── 部署目录 ──")
    d = repo_dir(args)
    if env_path(d).exists():
        mode = get_env(d, "SKILL_EXEC_MODE", "live")
        gw_cfg = bool(get_env(d, "ANTHROPIC_API_KEY") or get_env(d, "ANTHROPIC_AUTH_TOKEN"))
        gw = "已配" if gw_cfg else "未配"
        planner = "已配" if get_env(d, "LLM_PROVIDER_API_KEY") else "未配"
        say(f"✓ {d}（SKILL_EXEC_MODE={mode}；Skill 网关{gw}；规划器{planner}）")
    else:
        say(f"ℹ {d} 尚未初始化（openair up 会克隆并生成 .env）")

    if ok:
        say("\n体检通过，可以 `openair up`。")
    else:
        say("\n存在 ✗ 项，请先处理后再 `openair up`。")
    sys.exit(0 if ok else 1)


# ---------------------------------------------------------------- 入口

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001,S110,S112 — 老版本 Python 无 reconfigure，按默认编码即可
            pass
    ap = argparse.ArgumentParser(
        prog="openair",
        description="OpenAIR 科研智能体平台 · 一键启动器（up/config/status/logs/seed/down/doctor）")
    ap.add_argument("--version", action="version", version=f"openair-cli {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("up", help="体检 → 克隆/更新仓库 → 环境配置引导 → 启动全栈 → 打开浏览器")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.add_argument("--mode", choices=["live", "replay"], help="执行模式（写入 .env）")
    p.add_argument("--seed", action="store_true", help="启动时灌入演示数据")
    p.add_argument("--admin-email", help="首次生成 .env 时的管理员邮箱")
    p.add_argument("--admin-password", help="首次生成 .env 时的管理员密码（缺省自动生成）")
    p.add_argument("--no-open", action="store_true", help="启动后不打开浏览器")
    p.add_argument("--no-start", action="store_true", help="只准备仓库与 .env，不启动容器")
    p.add_argument("--yes", action="store_true", help="跳过交互式配置向导（沿用 .env 现值）")
    p.set_defaults(fn=cmd_up)

    p = sub.add_parser("config", help="交互式重配 .env（执行模式 / 模型网关 / 可选密钥）")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("status", help="查看容器状态与 API 健康")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("logs", help="跟踪容器日志（默认全部、持续跟随）")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.add_argument("service", nargs="?", help="服务名（api/web/db/minio/redis/worker…，缺省全部）")
    p.add_argument("--tail", type=int, default=120, help="先显示最近 N 行（默认 120）")
    p.add_argument("--no-follow", action="store_true", help="只显示尾部，不持续跟随")
    p.set_defaults(fn=cmd_logs)

    p = sub.add_parser("seed", help="灌入演示数据")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("down", help="停止全栈")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.add_argument("--volumes", action="store_true", help="连同数据卷删除（清空所有数据）")
    p.set_defaults(fn=cmd_down)

    p = sub.add_parser("doctor", help="环境体检（Docker / compose / 端口 / 磁盘 / 镜像加速）")
    p.add_argument("--dir", help="仓库目录（默认 ./OpenAIR）")
    p.set_defaults(fn=cmd_doctor)

    try:
        args = ap.parse_args()
        args.fn(args)
    except KeyboardInterrupt:
        say("\n[openair] 已中断。当前状态可用 `openair status` 查看")
        sys.exit(130)


if __name__ == "__main__":
    main()

# ===========================================================================
# OpenAIR API Server Image (compose / local)
# Orchestrates task execution, manages Docker workers, serves REST API.
#
# Zeabur / cloud MUST use the repo-root Dockerfile.api instead of this file.
#
# Build (from repo root):
#   docker build -f services/api/Dockerfile -t openair-api:latest .
# ===========================================================================

# Claude Code CLI 2.x 的 npm 包是自包含原生 ELF 二进制（运行时不依赖 node）。
# 平台子包 tarball（~130MB）在本网络路径上整文件下载必然 ECONNRESET，且 npm
# 会静默跳过失败的 optional dependency，留下不可用的占位桩 —— 因此用
# scripts/install_claude_cli.mjs 分块下载 + shasum 校验安装（见脚本头注释）。
# 最终镜像只拷 /usr/local/bin/claude 二进制，与仓库根 Dockerfile.api 同一模式。
FROM node:22-bookworm-slim AS claude-cli

COPY scripts/install_claude_cli.mjs /tmp/install_claude_cli.mjs
RUN node /tmp/install_claude_cli.mjs

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENAIR_REPO_ROOT=/app

# Debian 国产镜像源（清华）
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g; s/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' \
        /etc/apt/sources.list.d/debian.sources

# System deps. claude CLI 由 claude_agent_sdk stdio transport 拉起（durable
# claude channel 与健康检查在 PATH 上找 `claude`，见 scripts/claude-cli-smoke.sh
# 的开机后验证）；CLI 为原生二进制，无需 nodejs/npm。
# jq: vasp-stability run_hpc.sh 解析 jcc CLI JSON 输出。
# fonts-noto-cjk: S9 matplotlib 数据图的中文字形（无 CJK 字体时标注为方框）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
        bash git curl ca-certificates jq fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI（自 claude-cli 阶段拷贝原生二进制）。认证仅走环境变量
# （ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL 由 compose 注入）——容器内无
# login/onboarding。
COPY --from=claude-cli /usr/local/bin/claude /usr/local/bin/claude
RUN claude --version

WORKDIR /app

# Shared LLM core must be present before/with dependency install so
# `import llm_core` works in image, tests, and production (not editable:
# editable .pth breaks when build context paths differ from runtime).
COPY packages/llm_core/ /app/packages/llm_core/

# Python dependencies (PyPI) + in-repo llm_core
COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir /app/packages/llm_core \
    && python -c "import llm_core; print('llm_core_ok', llm_core.__file__); import mp_api; print('mp_api_ok', mp_api.__file__)"

# Application code
COPY services/api/app/ /app/app/

# Durable worker package (research-start / durable-run / dispatch claim loop).
# Required by `python -m worker.entrypoint` — without this, compose queues
# research-start requests that nobody ever claims.
COPY services/api/worker/ /app/worker/

# DB migrations (postgres_migrations.py reads /app/migrations at boot)
COPY services/api/migrations/ /app/migrations/

# Remaining shared packages (TS shared etc.; llm_core already copied above)
COPY packages/ /app/packages/

# Schemas (spec templates, pack configs)
COPY schemas/ /app/schemas/

# Replay fixtures (replay_runner reads repo_root()/fixtures/skill_io/<skill>;
# without this COPY the container cannot replay skills)
COPY fixtures/ /app/fixtures/

# Skills source tree (registry + per-skill skill.yaml). _load_skill_meta reads
# each skill.yaml for output_file / claude_name / input_skills etc., so the API
# needs the full tree — copying only registry.yaml leaves output_file unresolved
# and no substage artifact ever gets registered.
COPY skills/ /app/skills/

# Linux jcc CLI into PATH（vasp_stability_durable S7 提交 HPC 用）。
# 源文件是静态链接 ELF（vasp-band-gap/assets/jcc），git 不保留可执行位，
# 镜像内显式 chmod。v0.1.0 公开仓库剥离了该二进制资产——存在才链接，
# 缺失则跳过（容器内 JCC 采集本就依赖 JCC_CLI_PATH 指向外部构建，
# 见 docker-compose.yml 对应注释）。
RUN if [ -e /app/skills/materials/vasp-band-gap/assets/jcc ]; then \
        chmod +x /app/skills/materials/vasp-band-gap/assets/jcc \
        && ln -sf /app/skills/materials/vasp-band-gap/assets/jcc /usr/local/bin/jcc; \
    fi

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run API server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

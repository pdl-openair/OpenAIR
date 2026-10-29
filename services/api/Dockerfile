# ===========================================================================
# OpenAIR API Server Image (compose / local)
# Orchestrates task execution, manages Docker workers, serves REST API.
#
# Zeabur / cloud MUST use the repo-root Dockerfile.api instead of this file.
#
# Build (from repo root):
#   docker build -f services/api/Dockerfile -t openair-api:latest .
# ===========================================================================

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENAIR_REPO_ROOT=/app

# Debian 国产镜像源（清华）
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g; s/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' \
        /etc/apt/sources.list.d/debian.sources

# System deps (nodejs is required by the claude CLI spawned by claude_agent_sdk
# stdio transport — the durable claude channel and health check resolve `claude`
# on PATH; see scripts/claude-cli-smoke.sh for post-boot verification).
# jq: vasp-stability run_hpc.sh 解析 jcc CLI JSON 输出。
# fonts-noto-cjk: S9 matplotlib 数据图的中文字形（无 CJK 字体时标注为方框）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git curl ca-certificates nodejs npm jq fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (npm global; npmmirror keeps the build reproducible in CN
# networks). Authentication is env-only (ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL
# injected by compose) — no login/onboarding happens inside the container.
RUN npm install -g @anthropic-ai/claude-code --registry=https://registry.npmmirror.com \
    && claude --version

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

# Skills source tree (registry + per-skill skill.yaml). _load_skill_meta reads
# each skill.yaml for output_file / claude_name / input_skills etc., so the API
# needs the full tree — copying only registry.yaml leaves output_file unresolved
# and no substage artifact ever gets registered.
COPY skills/ /app/skills/

# Linux jcc CLI into PATH（vasp_stability_durable S7 提交 HPC 用）。
# 源文件是仓库内静态链接 ELF（vasp-band-gap/assets/jcc）；git 不保留
# 可执行位，镜像内显式 chmod。
RUN chmod +x /app/skills/materials/vasp-band-gap/assets/jcc \
    && ln -sf /app/skills/materials/vasp-band-gap/assets/jcc /usr/local/bin/jcc

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run API server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

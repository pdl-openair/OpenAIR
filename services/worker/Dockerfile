# ===========================================================================
# OpenAIR Worker Image (Alpine — fast build, small footprint)
# Runs Claude Code in an isolated container per task execution.
#
# Build (from repo root):
#   docker build -f services/worker/Dockerfile -t openair-worker:latest .
# ===========================================================================
FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENAIR_REPO_ROOT=/app

# Alpine 国产镜像源（清华）
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apk/repositories

# System dependencies: git + Node.js (Alpine 仓库自带) + su-exec (privilege drop).
# Build deps (gcc/g++/musl-dev) are kept installed: pymatgen/scipy/spglib may
# need to compile wheels on Alpine (musllinux) when binary wheels are missing
# for a given platform/version combo. Removing them post-install saves image
# size but breaks pip install on subsequent dep changes.
RUN apk update && \
    apk add --no-cache git nodejs npm su-exec \
        gcc g++ musl-dev linux-headers openblas-dev freetype-dev && \
    rm -rf /var/cache/apk/*

# Install Claude Code CLI (native Linux binary)
RUN npm install -g @anthropic-ai/claude-code && \
    claude --version

# Non-root user: Claude Code refuses --dangerously-skip-permissions (bypassPermissions)
# when run as root/sudo. The worker must execute as an unprivileged user.
RUN addgroup -S openair && \
    adduser -S -G openair -u 1000 -h /home/openair -s /bin/sh openair && \
    mkdir -p /home/openair/.claude && \
    chown -R openair:openair /home/openair

ENV HOME=/home/openair

WORKDIR /app

# Shared LLM core (U-05): llm_core.token_usage / openai_compatible_client / claude_turn.
# Non-editable install so production images do not depend on editable .pth paths.
COPY packages/llm_core/ /app/packages/llm_core/
COPY services/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir /app/packages/llm_core \
    && python -c "import llm_core; print('llm_core_ok', llm_core.__file__)"

# --- Application code ---

# Worker module
COPY services/worker/worker/ /app/worker/

# Shared API modules that the worker imports
COPY services/api/app/execution/ /app/app/execution/
COPY services/api/app/config.py /app/app/config.py
COPY services/api/app/modules/ /app/app/modules/

# Schemas directory (spec templates, packs config, etc.)
COPY schemas/ /app/schemas/

# --- System Skills (baked into image, read-only) ---
COPY skills/ /app/_skills_src/
COPY services/worker/sync_skills.py /app/sync_skills.py
RUN python /app/sync_skills.py && rm -rf /app/_skills_src /app/sync_skills.py

# Ensure user-skills directory exists and is writable by openair (runtime MinIO download)
RUN mkdir -p /app/.claude/user-skills && \
    chown -R openair:openair /app/.claude/user-skills

# Ensure workspace root exists (mounted at runtime; ownership fixed in entrypoint)
RUN mkdir -p /workspace

# Privilege-drop entrypoint: fixes bind-mounted volume ownership as root, then
# runs the worker as the unprivileged `openair` user.
COPY services/worker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "worker.entrypoint"]

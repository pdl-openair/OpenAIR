# ===========================================================================
# OpenAIR Web Frontend Image (compose / local)
# Vite + React SPA built with pnpm in a monorepo, then served by nginx.
# nginx also reverse-proxies /api → api:8000 (same-origin → no CORS, no
# baked backend URL; getApiBaseUrl() returns "" in production builds).
#
# Zeabur / cloud MUST use the repo-root Dockerfile.web instead of this file
# (cross-origin via VITE_OPENAIR_API_BASE_URL).
#
# Build (from repo root):
#   docker build -f apps/web/Dockerfile -t openair-web:latest .
# ===========================================================================

# ---------------------------------------------------------------------------
# Stage 1 — build the SPA
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

ENV CI=1 \
    NODE_OPTIONS=--max-old-space-size=4096

# 国产 npm 镜像（淘宝）+ 通过 corepack 锁定 pnpm 版本（与根 package.json 一致）
RUN npm config set registry https://registry.npmmirror.com && \
    corepack enable && corepack prepare pnpm@9.15.0 --activate

WORKDIR /repo

# 先只拷 workspace 清单 + lockfile，最大化 pnpm install 层缓存
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY packages/shared/package.json packages/shared/package.json
COPY apps/web/package.json apps/web/package.json

RUN pnpm install --frozen-lockfile

# 拷源码：shared 作为 vite alias 源被直接打包；web 是构建目标
# fixtures 经 vite @fixtures alias 被部分 fixture 数据模块导入，构建期必须存在
COPY packages/shared packages/shared
COPY apps/web apps/web
COPY fixtures fixtures

# 生产构建默认同源（见 apps/web/src/api/http.ts），无需注入 VITE 变量
RUN pnpm --filter @openair/web build

# ---------------------------------------------------------------------------
# Stage 2 — nginx runtime（使用已备好的本地镜像）
# ---------------------------------------------------------------------------
FROM nginx:1.30.2-alpine

# 替换默认 server 块
RUN rm -f /etc/nginx/conf.d/default.conf
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

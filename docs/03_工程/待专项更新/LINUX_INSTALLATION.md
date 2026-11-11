# OpenAIR Linux 安装与本地开发

> 状态：已确认
> 更新时间：2026-11-09
> 用途：在 Linux 上完成 OpenAIR 全栈安装，或启动可以修改代码的本地开发环境。

本文命令在 Ubuntu 26.04、Docker 29.1.3、Compose 2.40.3、Node 24、pnpm 9.15.0、uv 0.12.17 上验证。验证机为 AMD Ryzen Threadripper 9960X（48 线程）、60 GiB 内存、NVIDIA RTX 3090。其他 Linux 发行版只需替换系统包管理命令。

## 1. 选择安装方式

| 方式 | 适合 | 需要 | 访问地址 |
| --- | --- | --- | --- |
| Docker 全栈 | 首次体验、演示、接近生产的部署 | Docker Engine 24+、Compose v2 | 工作台 `http://localhost:8080`，API `http://localhost:8000` |
| 本地开发 | 修改前后端代码、调试 Worker | Docker 全栈所需工具，另加 Node 20+、pnpm 9、Python 3.12+、uv | 工作台 `http://localhost:5173`，API `http://localhost:8000` |

仓库根目录的 `docker-compose.yml` 是两种方式的共同基础。全栈方式启动全部服务，本地开发方式只用它启动 PostgreSQL 和可选的 MinIO 兼容存储。

## 2. 安装系统依赖

### 2.1 Docker Engine 与 Compose

Ubuntu 与 Debian 使用 Docker 官方仓库：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
newgrp docker
```

`newgrp` 只对当前终端生效。重新登录后，普通用户可以直接执行 `docker` 命令。

验证：

```bash
docker --version
docker compose version
docker run --rm hello-world
```

无法访问 Docker 官方仓库时，可以先用发行版仓库安装 `docker.io` 与 `docker-compose-v2`，或按仓库 README 的国内网络环境配置为 Docker 设置镜像加速。

### 2.2 Node、pnpm、Python 与 uv

Node 20 以上，仓库通过 `packageManager` 固定 pnpm 9.15.0：

```bash
node --version
npm install -g pnpm@9.15.0
pnpm --version
```

Python 3.12 以上，依赖统一由 uv 管理：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
uv python install 3.12
```

安装脚本把 `uv` 放到 `~/.local/bin`。如果当前 shell 找不到 `uv`，重新登录或把该目录加入 `PATH`。

## 3. Docker 全栈安装

### 3.1 克隆并创建配置

```bash
git clone https://github.com/pdl-openair/OpenAIR.git
cd OpenAIR
cp .env.example .env
```

`.env.example` 的注释全部独立成行，可以整份复制。`.env` 已被 git 忽略，不要提交。

### 3.2 填写必填变量

Compose 强制要求以下四项，模板默认值只能用于本机：

| 变量 | 用途 | 生成方式 |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL 密码，API 与 Worker 必须一致 | `openssl rand -hex 16` |
| `OPENAIR_MINIO_ROOT_PASSWORD` | 对象存储管理员密码 | `openssl rand -hex 16` |
| `OPENAIR_JWT_SECRET_KEY` | 登录 JWT 签名密钥 | `openssl rand -hex 32` |
| `OPENAIR_WORKER_AUTH_TOKEN` | API 与 Worker 互认口令 | `openssl rand -hex 32` |

把生成值写入 `.env` 对应行。对外提供服务时，四项都必须使用随机值。

同时设置管理员账号。Compose 以生产口径强制登录，没有管理员账号无法进入工作台：

```dotenv
OPENAIR_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
OPENAIR_BOOTSTRAP_ADMIN_PASSWORD=change-me-please
OPENAIR_BOOTSTRAP_ADMIN_USERNAME=ops
```

密码至少 8 位。首次启动时 API 会创建或提权该账号。邮箱必须通过 `email-validator` 校验，`.local` 等保留域名会被拒绝，建议使用 `example.com` 或真实域名。

### 3.3 选择执行模式

第一次体验建议使用零密钥回放：

```dotenv
SKILL_EXEC_MODE=replay
```

回放模式读取仓库内 fixtures，不需要模型网关密钥。需要真实模型能力时改为 `live`，并填写规划器与 Skill 网关：

```dotenv
LLM_PROVIDER_BASE_URL=https://your-openai-compatible-endpoint
LLM_PROVIDER_API_KEY=your-key
OPENAIR_CLAUDE_MODEL=claude-sonnet-4-5
```

Compose 部署下，容器内的 `ANTHROPIC_*` 会自动回退到 `LLM_PROVIDER_*`，通常填这一套即可。两者的详细区别见 `.env.example` 和 README 的按需配置分组。

### 3.4 构建并启动

```bash
docker compose up -d --build
```

首次构建需要下载基础镜像并安装前后端依赖，通常几分钟到十几分钟。构建完成后查看服务状态：

```bash
docker compose ps
```

预期看到 `postgres`、`minio`、`redis`、`api`、`durable-worker`、`web` 均为运行状态。

如果构建阶段出现下载中断或连接重置，可以设置：

```dotenv
OPENAIR_BUILD_NETWORK=host
```

然后重新执行 `docker compose up -d --build`。

### 3.5 验证安装

```bash
curl -fsS http://127.0.0.1:8000/health
```

返回 JSON 中包含 `"status":"ok"` 表示 API 正常。工作台与对象存储控制台：

| 服务 | 地址 |
| --- | --- |
| 工作台 | `http://localhost:8080` |
| API 健康检查 | `http://localhost:8000/health` |
| 对象存储控制台 | `http://localhost:9001` |

用 `.env` 中的管理员邮箱与密码登录工作台。

对象存储使用 `pgsty/silo`，它是 MinIO 社区版的兼容分支。控制台、S3 端口、`MINIO_*` 环境变量与磁盘格式都与 MinIO 一致，账号为 `OPENAIR_MINIO_ROOT_USER`，密码为 `OPENAIR_MINIO_ROOT_PASSWORD`。

### 3.6 查看日志与停止

```bash
docker compose logs -f api
docker compose logs -f durable-worker
docker compose ps
docker compose down
```

`docker compose down` 保留数据库与对象存储数据。只有确认要删除全部本地数据时才执行：

```bash
docker compose down -v
```

## 4. 本地开发安装

本地开发不构建整套镜像，前后端直接在宿主机运行，PostgreSQL 仍由 Compose 提供。

### 4.1 安装依赖

仓库根目录：

```bash
pnpm install --frozen-lockfile
```

后端：

```bash
cd services/api
uv sync --python 3.12
cd ../..
```

`uv sync` 会创建 `services/api/.venv`，并把 `packages/llm_core` 以可编辑方式安装进去。`import llm_core` 是 API 导入期的硬依赖，缺少时启动直接报错。

### 4.2 启动 PostgreSQL

```bash
docker compose up -d postgres
```

数据库监听 `localhost:15432`。默认产物存本地盘，不需要 MinIO。启用 `OPENAIR_ARTIFACT_STORAGE=minio` 时再启动：

```bash
docker compose up -d minio
```

MinIO 兼容存储监听 `localhost:9000`，控制台监听 `localhost:9001`。

### 4.3 启动 API、Worker 和前端

分别在三个终端执行。API：

```bash
cd services/api
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 10
```

Worker：

```bash
cd services/api
uv run python -m worker.entrypoint
```

前端，在仓库根目录：

```bash
pnpm --filter @openair/web dev
```

API 与 Worker 读取同一份仓库根目录 `.env`，并且必须使用同一份工作树。只启动 API 不启动 Worker 时，任务会停留在排队状态。

### 4.4 验证本地开发环境

```bash
curl -fsS http://127.0.0.1:8000/health
curl -I http://127.0.0.1:5173
```

健康检查返回的 JSON 中包含 `"status":"ok"`，前端返回 HTTP 200 即可。浏览器打开 `http://localhost:5173`。

前端通过根目录 `.env` 的 `VITE_OPENAIR_API_BASE_URL` 找到 API，默认值为 `http://127.0.0.1:8000`。

### 4.5 停止

API、Worker、前端各自按 `Ctrl+C`。停止基础设施：

```bash
docker compose stop postgres minio
```

## 5. Windows 命令与 Linux 命令对照

仓库现有本地开发文档以 Windows PowerShell 为主，Linux 对应关系如下：

| Windows | Linux |
| --- | --- |
| `Copy-Item .env.example .env` | `cp .env.example .env` |
| `pnpm.cmd --filter @openair/web dev` | `pnpm --filter @openair/web dev` |
| `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| `Invoke-RestMethod http://localhost:8000/health` | `curl http://localhost:8000/health` |
| `netstat -ano \| findstr :8000` | `ss -ltnp \| grep :8000` |
| `setup.bat` 与 `doctor.ps1` | 按本文手动执行同等步骤 |

`setup.bat` 与 `dev-doctor` 只提供 Windows 实现，Linux 暂无一键脚本。

## 6. 常见问题

| 现象 | 检查 |
| --- | --- |
| `permission denied` 访问 `/var/run/docker.sock` | 执行 `sudo usermod -aG docker "$USER"` 后重新登录，或临时使用 `sudo docker compose` |
| 端口被占用 | `ss -ltnp \| grep -E ':(8000|8080|15432|9000|9001)'` 找到进程；在 `.env` 改 `OPENAIR_API_PORT`、`OPENAIR_WEB_PORT`、`OPENAIR_POSTGRES_PORT`，本地开发时先停掉 Docker 里的同名服务 |
| 拉取镜像超时或被拒绝 | 检查 `/etc/docker/daemon.json` 的 `registry-mirrors`；镜像加速地址会失效，换用当前可用的地址后执行 `sudo systemctl restart docker` |
| 构建时下载依赖中断 | 在 `.env` 设置 `OPENAIR_BUILD_NETWORK=host` 后重跑 `docker compose up -d --build` |
| 前端构建内存不足 | 构建阶段需要约 4 GB 内存；`NODE_OPTIONS=--max-old-space-size=4096` |
| API 启动报 `ModuleNotFoundError: llm_core` | 在 `services/api` 下重新执行 `uv sync --python 3.12`，不要只安装 `requirements.txt` |
| `uv sync` 选择了 Python 3.14 或 3.15 | 显式加 `--python 3.12` |
| API 能启动但任务不执行 | Worker 未启动，或 API 与 Worker 使用了不同的工作树、`.env` |
| 登录后接口 401 | `.env` 的管理员账号与密码是否生效；修改账号配置后需要重启 API |
| 对象存储控制台登录失败 | `OPENAIR_MINIO_ROOT_USER` 与 `OPENAIR_MINIO_ROOT_PASSWORD` 是否已写入 `.env`，修改后需要重启 `minio` 服务 |

## 7. 相关文档

- [本地开发（Windows）](LOCAL_DEVELOPMENT.md)
- [开发入门](GETTING_STARTED.md)
- 仓库根目录 [README.md](../../../README.md) 的快速开始与部署指南
- 仓库根目录 [.env.example](../../../.env.example) 的逐项配置说明

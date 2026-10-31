# OpenAIR 本地开发

> 状态：已确认  
> 更新时间：2026-08-28  
> 用途：帮助开发者在 Windows 本地启动 OpenAIR，并区分日常开发环境和最新 `origin/test` 验证环境。

## 1. 我该用哪种方式

| 目的 | 方式 | Web |
| --- | --- | --- |
| 修改和调试当前代码 | **日常开发模式** | `http://localhost:5173` |
| 运行最新 `origin/test` 完整系统 | **全 Docker 模式** | `http://localhost:8080` |

日常开发模式运行当前工作树；全 Docker 模式运行最新 `origin/test`。

## 2. 日常开发：4 步启动

### 第 1 步｜准备环境

需要：Git、Docker Desktop / Docker Compose v2、Node.js 20+、Python 3.12+、`pnpm`、`uv`。

首次使用：

```powershell
Copy-Item .env.example .env
```

按 `.env.example` 填写当前任务需要的配置和密钥，不要提交 `.env`。

### 第 2 步｜安装依赖

仓库根目录：

```powershell
corepack pnpm install --frozen-lockfile
```

后端：

```powershell
cd services/api
uv sync
cd ../..
```

Python 环境位于 `services/api/.venv`。

### 第 3 步｜启动 PostgreSQL

日常开发默认只需要：

```powershell
docker compose up -d postgres
```

PostgreSQL：`localhost:15432`

如果启用 `OPENAIR_ARTIFACT_STORAGE=minio`，还需要在 `.env` 配置 MinIO access key 和 secret key，并启动：

```powershell
docker compose up -d minio
```

MinIO API：`http://localhost:9000`  
MinIO Console：`http://localhost:9001`

Redis 当前不是日常开发必需服务。

### 第 4 步｜启动 API、Worker 和 Web

分别打开 **3 个 PowerShell 终端**。

API：

```powershell
& .\services\api\.venv\Scripts\Activate.ps1
Set-Location services/api
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Worker：

```powershell
& .\services\api\.venv\Scripts\Activate.ps1
Set-Location services/api
python -m worker.entrypoint
```

API 和 Worker 使用同一工作树和同一份根目录 `.env`。

Web 在仓库根目录启动：

```powershell
pnpm.cmd --filter @openair/web dev
```

访问：

```text
Web    http://localhost:5173
API    http://localhost:8000
Health http://localhost:8000/health
```

## 3. 运行最新 test 版本

仓库根目录必须已有可用的 `.env`。

启动：

```powershell
docker compose up -d
```

当前只支持无参数启动和 `stop`；`quick` 已禁用。

启动器会：

```text
获取最新 origin/test
→ 创建或刷新 <源仓库路径>-runtime-test
→ 复制源仓库 .env
→ 构建 Docker 镜像
→ 再次确认 origin/test 未变化
→ 启动完整系统
→ 执行启动自检
```

材料 MCP 类 skill 需要时额外配置（见 `.env.example`）：

```text
# MATERIALS_MCP_BASE_URL=<your-mcp-endpoint>
```

不配置则材料 MCP 类 skill 不可用，其余功能不受影响。

runtime worktree 是一次性运行环境，启动器可能执行：

```text
checkout --force
reset --hard
clean -ffdx
```

不要在其中保存代码或临时文件。

启动自检还会检查：

- `run.sh` 使用 LF 换行；
- adapter 存在正确的 `EVOLVE_PY=sys.executable` wiring。

任一检查失败都会使启动失败。

如遇网络波动导致构建失败，重跑启动命令即可。

当前固定探测 API `8000`、Web `8080`，不要修改这两个端口。

访问：

```text
Web    http://localhost:8080
API    http://localhost:8000
Health http://localhost:8000/health
```

## 4. 怎么确认启动成功

日常开发：

```powershell
Invoke-RestMethod http://localhost:8000/health
docker compose ps
```

确认 Web、API、Worker、PostgreSQL，以及当前实际启用的其他服务正常。

全 Docker：

```powershell
Invoke-RestMethod http://localhost:8000/health
docker compose -p openair ps
```

Compose 项目名由目录名决定（默认 `openair`）。

## 5. 怎么停止

日常开发：API、Worker、Web 对应终端按 `Ctrl+C`。

停止 PostgreSQL：

```powershell
docker compose stop postgres
```

如启动了 MinIO：

```powershell
docker compose stop minio
```

停止当前目录对应的 Compose 项目：

```powershell
docker compose down
```

全 Docker：

```powershell
docker compose stop api durable-worker web
```

这个命令只停止 `api`、`durable-worker`、`web`。

完整停止：

```powershell
docker compose -p openair down
```

只有明确需要删除本地持久化数据时才使用：

```powershell
docker compose -p openair down -v
```

它会删除 `pgdata`、`minio_data`、`redis_data`、`workspace_data`。

## 6. 最容易踩的坑

| 现象 | 先检查 |
| --- | --- |
| 修改代码后页面没有变化 | 是否误用了全 Docker `origin/test` 模式 |
| API 能启动但任务不执行 | Worker 是否启动，并与 API 使用同一工作树和 `.env` |
| PostgreSQL 连接失败 | `docker compose ps`、`15432` 和 `.env` |
| MinIO 连接失败 | 是否已启用 MinIO，并配置 access key / secret key |
| Web 无法调用 API | 根目录 `.env` 中的 `VITE_OPENAIR_API_BASE_URL`；默认指向 `http://127.0.0.1:8000` |
| 全 Docker 启动失败 | `.env`、Materials MCP、bootstrap 自检、`origin/test` 是否变化 |
| `docker compose ps` 看不到全 Docker 服务 | 使用 `docker compose -p openair ps` |

如果本文档与当前仓库不一致，以以下文件为准：

```text
docker-compose.yml
.env.example
services/api/pyproject.toml
package.json
apps/web/vite.config.ts
```

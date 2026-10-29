# services/api

OpenAIR 后端：**FastAPI**。

- **本地开发**：进程内内存 Store + 可选 fixture 灌库。
- **云端部署**：PostgreSQL 持久化 + Bearer Token 认证 + 结构化日志。

---

## 目录

- [启动](#启动)
- [环境变量参考](#环境变量参考)
- [认证与多用户](#认证与多用户)
- [持久化](#持久化)
- [执行运行时边界](#执行运行时边界api-vs-runtime)
- [Artifact 存储分层](#artifact-存储分层)
- [Agent / Claude Code](#agent--claude-code)
- [观测与排障](#观测与排障)
- [成本控制](#成本控制)
- [开发演示数据](#开发演示数据可选)
- [接口列表](#接口列表)
- [模块](#模块)
- [相关文档](#相关文档)

---

## 启动

**本地开发**

```bash
cd services/api
uv sync
# Windows（推荐，自动设置 ProactorEventLoop）：
uv run python run_dev.py
# Linux/macOS：
uv run uvicorn app.main:app --reload --port 8000
# 无 uv 时：python -m pip install -r requirements-local.txt，再用当前 Python 启动。
```

启动日志应出现：`[startup] env=development auth=open persistence=postgres runtime=inline ...`

**云端部署**

```bash
cd services/api
uv sync
OPENAIR_ENV=production \
OPENAIR_AUTH_REQUIRED=1 \
OPENAIR_AUTH_TOKEN=<your-secret> \
OPENAIR_LOG_FORMAT=json \
OPENAIR_COST_TRACKING=1 \
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

生产环境建议使用进程管理器（systemd / supervisord / Docker）。

**健康检查**

```bash
curl http://localhost:8000/health
# → {"status":"ok"}

curl http://localhost:8000/health/agent
# → Claude CLI 路径、执行模式、运行时边界

curl -H "Authorization: Bearer <token>" http://localhost:8000/health/diagnostics
# → 完整诊断：配置、任务数、成本计数
```

---

## 环境变量参考

完整参考见仓库根目录 `.env.example`。以下按类别汇总：

### 核心配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAIR_ENV` | `development` | `development` 或 `production`。影响 dev seed、日志默认值 |
| `OPENAIR_API_PORT` | `8000` | API 监听端口 |
| `SKILL_EXEC_MODE` | `live` | `live`（调用 Claude CLI）或 `replay`（读 fixture） |

### 认证（Cloud-02）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAIR_AUTH_REQUIRED` | `0` | `1` = 要求 Bearer Token；`0` = 开放模式（仅开发） |
| `OPENAIR_AUTH_TOKEN` | `openair-dev-token` | Auth Required 时的验证凭证。**云端必须改** |
| `OPENAIR_DEFAULT_OWNER_ID` | `user_demo_owner` | 开放模式下的默认 ownerId |
| `OPENAIR_DEFAULT_WORKSPACE_ID` | `ws_demo_default` | 开放模式下的默认 workspaceId |
| `OPENAIR_DEFAULT_CREATED_BY` | `openair_system` | 开放模式下的默认 createdBy |

### 持久化（Cloud-03）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAIR_PERSISTENCE_BACKEND` | `postgres` | `postgres`（推荐）或 `json`（仅本地回退） |
| `POSTGRES_HOST` | `localhost` | PostgreSQL 主机 |
| `POSTGRES_PORT` | `5432` | PostgreSQL 端口 |
| `POSTGRES_USER` | `root` | PostgreSQL 用户 |
| `POSTGRES_PASSWORD` | `password` | PostgreSQL 密码。**云端必须改** |
| `POSTGRES_DATABASE` | `openair` | PostgreSQL 数据库名 |

### 执行运行时（Cloud-04）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAIR_RUNTIME_TYPE` | `inline` | `inline`（API 进程内）或 `worker`（Phase-1 stub） |
| `OPENAIR_WORKSPACE_ROOT` | `<repo>/workspace` | 任务工作区根目录。云端应设为独立卷 |
| `OPENAIR_CLAUDE_PERMISSION_MODE` | `bypassPermissions` | Claude Code 权限模式 |
| `OPENAIR_ALLOW_LOCAL_CLI_IN_PROD` | `0` | 生产是否允许本地 CLI |
| `OPENAIR_CLAUDE_CLI_PATH` | — | Claude CLI 路径（仅 PATH 找不到时） |

### 观测（Cloud-07）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAIR_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `OPENAIR_LOG_FORMAT` | `text` | `text`（人类可读）或 `json`（结构化，云端推荐） |
| `OPENAIR_COST_TRACKING` | `0` | `1` = 记录粗粒度成本指标 |

---

## 认证与多用户

Phase-1 使用 **静态 Bearer Token** 认证：

1. 设置 `OPENAIR_AUTH_REQUIRED=1` 和一个安全的 `OPENAIR_AUTH_TOKEN`。
2. 所有 `/api/*` 请求必须携带 `Authorization: Bearer <token>`。
3. 可通过 `X-OpenAIR-Owner-Id` / `X-OpenAIR-Workspace-Id` 请求头覆盖默认身份。
4. 任务自动按 `ownerId` / `workspaceId` 隔离；用户只能看到自己范围内的任务。

**开放模式**（`OPENAIR_AUTH_REQUIRED=0`）：所有请求使用默认身份，适合本地开发和演示。

---

## 持久化

Phase-1 支持两种持久化后端：

### PostgreSQL（推荐，云端必须）

```bash
OPENAIR_PERSISTENCE_BACKEND=postgres
```

- 任务、消息、能力调用、确认请求、artifact 元数据均以 JSONB 存储在 PostgreSQL。
- 自动在启动时 bootstrap 数据库表。
- 支持重启恢复：服务重启后所有数据仍可读取。

### JSON 文件（本地回退）

```bash
OPENAIR_PERSISTENCE_BACKEND=json
```

- 存储在 `<repo>/data/tasks/*.json`。
- 仅适合本地开发，不适合多实例或云端部署。

---

## 执行运行时边界（API vs Runtime）

Phase-1 将执行层拆分为明确的 **API 编排层** 与 **Runtime 执行层**：

- **API 层**（`app.execution.agent_service`）：负责任务状态管理、消息记录、artifact 注册、capability call 生命周期、失败/重试决策。
- **Runtime 层**（`app.execution.runtime_boundary`）：负责实际的 LLM/CLI 调用、工作目录、超时控制。

Runtime 类型（`OPENAIR_RUNTIME_TYPE`）：

| 值 | 说明 | 适用场景 |
|---|---|---|
| `inline`（默认） | API 进程内直接调用 Claude Code CLI 或 replay fixtures | 本地开发、简单单节点部署 |
| `worker` | **Phase-1 stub**。预留外部 worker/runtime 接口。 | 云端正式部署的后续演进方向 |

**本地开发路径**：`inline` + `live` 仍要求宿主机安装并登录 Claude Code CLI，这是 Phase-1 的本地开发便利，不是正式云端执行契约。

**云端路径**：`worker`（stub）+ 独立工作区 + 外部 runtime 服务。完整实现超出 Phase-1 范围。

---

## Artifact 存储分层

Phase-1 引入 Artifact 的 **metadata vs content/blob** 分层：

| `storageType` | 说明 | Phase-1 行为 |
|---|---|---|
| `inline` | 内容内联在 artifact metadata | 小文本文件直接存 `inlineContent` |
| `local` | 内容存于本地文件系统 | 通过 `get_task_workspace_dir()` 解析 |
| `external` | 外部对象存储 | **Phase-1 stub**：字段预留 |

API `/artifacts/{id}/content` 的返回逻辑：
1. `storageType == "inline"` → 直接返回 `inlineContent`
2. `storageType == "local"` → 通过 `ArtifactResolver` 读取本地文件
3. `storageType == "external"` → **N/A：Phase-1 不实现**

前端不感知 storageType 差异，统一调用 `/content` 获取文本、`/raw` 获取二进制。旧数据无 `storageType` 时默认视为 `"local"`。

---

## Agent / Claude Code（工作台右侧）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tasks/{task_id}/agent/messages` | 交互历史 |
| POST | `/api/tasks/{task_id}/agent/chat` | 发送消息（Body: `{ "message": "..." }`） |
| POST | `/api/tasks/{task_id}/agent/chat/stream` | 流式聊天（`application/x-ndjson`） |
| POST | `/api/tasks/{task_id}/execute` | 执行 Skill（Body: `{ "skillId": "literature_review" }`） |
| GET | `/api/tasks/{task_id}/calls` | 能力调用列表 |
| GET | `/api/tasks/{task_id}/profile` | 任务元信息草稿 |
| POST | `/api/tasks/{task_id}/profile/summary` | 基于会话自动总结元信息 |
| PATCH | `/api/tasks/{task_id}/profile/confirm` | 用户确认/编辑元信息 |

本地需已安装并登录 Claude Code CLI。

- **Windows + 直接 `uvicorn`**：默认事件循环不支持子进程 → 请用 **`uv run python run_dev.py`**。
- **Windows + `OPENAIR_API_RELOAD=1`**：uvicorn reload 会切到 SelectorEventLoop，Claude 子进程不可用。
- **PATH 不含** `~\.local\bin`：设置 `OPENAIR_CLAUDE_CLI_PATH` 后重启。
- 诊断：`GET /health/agent` 查看 runtime 信息。

---

## 观测与排障

### 结构化日志

所有后端日志通过 `openair` logger 输出。生产环境建议使用 JSON 格式：

```bash
OPENAIR_LOG_FORMAT=json
```

JSON 日志格式：

```json
{"ts": "2024-01-15 10:30:00", "level": "INFO", "logger": "openair", "msg": "request GET /api/tasks → 200 (45ms) owner=user_001"}
```

### 请求追踪

每个 API 请求自动记录：

- HTTP 方法和路径
- 响应状态码
- 请求耗时（ms）
- 请求者 owner（从 auth 或 header 解析）

日志示例（text 格式）：

```
2024-01-15 10:30:00 INFO [openair] request GET /api/tasks → 200 (45ms) owner=user_001
2024-01-15 10:30:01 INFO [openair] request POST /api/tasks/task_001/agent/chat/stream → 200 (12340ms) owner=user_001
```

### 诊断端点

| 端点 | 认证 | 说明 |
|------|------|------|
| `GET /health` | 不需要 | 基础存活检查 |
| `GET /health/agent` | 不需要 | Claude CLI 状态、执行模式 |
| `GET /health/diagnostics` | 需要 | 完整运行时诊断：配置、任务数、成本计数 |

`/health/diagnostics` 返回示例：

```json
{
  "env": "production",
  "authRequired": true,
  "persistenceBackend": "postgres",
  "runtimeType": "inline",
  "skillExecMode": "live",
  "logLevel": "INFO",
  "logFormat": "json",
  "costTracking": true,
  "taskCount": 42,
  "costCounters": {
    "claude_api_calls": 156,
    "task_executions": 38,
    "artifact_bytes_written": 1048576
  }
}
```

### 常见排障场景

| 症状 | 排查方式 |
|------|----------|
| 前端 401 | 检查 `OPENAIR_AUTH_REQUIRED` 和 token 是否一致 |
| 前端 CORS 错误 | `OPENAIR_AUTH_REQUIRED=1` 时不放行 localhost；前端需部署到正式域名或通过反代 |
| 任务列表为空 | 检查 PostgreSQL 连接；`/health/diagnostics` 确认 `taskCount` |
| 工作台流式失败 | 检查 `/health/agent` 确认 `claudeCliPath`；Windows 用 `run_dev.py` |
| 服务重启后数据丢失 | 确认 `OPENAIR_PERSISTENCE_BACKEND=postgres`，检查 PG 连接 |
| 日志太多/太少 | 调整 `OPENAIR_LOG_LEVEL` |

---

## 成本控制

Phase-1 提供粗粒度成本观察：

```bash
OPENAIR_COST_TRACKING=1
```

启用后追踪以下指标（通过 `/health/diagnostics` 查看）：

| 指标 | 说明 |
|------|------|
| `claude_api_calls` | Claude API / CLI 调用总次数 |
| `task_executions` | 任务执行（Skill 执行）总次数 |
| `artifact_bytes_written` | Artifact 写入总字节数 |

**延后项**（N/A：Phase-1 只做粗粒度观察）：
- 精确 token 计数和费用计算
- 按用户/工作空间的成本分摊
- 自动预算告警和限流
- 成本 dashboard

---

## 开发演示数据（可选）

本地默认（`OPENAIR_ENV=development`）会在启动时把 `fixtures/recorded_runs` **一次性灌入内存**，便于看板/详情/工作台联调。

```powershell
# 强制关闭灌库
$env:OPENAIR_DEV_SEED_FIXTURES = "0"
```

生产请设 `OPENAIR_ENV=production` 或 `OPENAIR_DEV_SEED_FIXTURES=0`。

可选指定 recorded run 根目录：`OPENAIR_DEV_FIXTURES_DIR`。

前端仍可通过 Vite 直接引用 `fixtures/` 做离线展示；与后端运行时解耦。

---

## 接口列表（前缀 `/api`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/health/agent` | Agent 运行时诊断 |
| GET | `/health/diagnostics` | 完整运行时诊断（需认证） |
| GET | `/api/tasks` | 任务列表（按当前用户 scope） |
| GET | `/api/tasks/{task_id}` | 单任务 |
| POST | `/api/tasks` | 创建任务 |
| POST | `/api/tasks/draft` | 创建草稿任务 |
| POST | `/api/tasks/{task_id}/confirm-plan` | Confirm task plan; research-start drafts require a structured confirmation body |
| POST | `/api/tasks/{task_id}/research-start-decision` | Persist a non-start research decision (`supplement`, `modify_goal`, or `pause`) while keeping the task as a draft |
| POST | `/api/tasks/{task_id}/revise-plan` | 请求修改计划 |
| POST | `/api/tasks/{task_id}/cancel-draft` | 取消草稿 |
| POST | `/api/tasks/{task_id}/start` | 开始任务 |
| PATCH | `/api/tasks/{task_id}/status` | 更新任务 `status` |
| GET | `/api/tasks/{task_id}/artifacts/{id}/content` | Artifact 文本内容 |
| GET | `/api/tasks/{task_id}/artifacts/{id}/raw` | Artifact 二进制 |
| GET | `/api/tasks/{task_id}/artifacts/{id}/tree` | Artifact 目录树 |
| POST | `/api/tasks/{task_id}/stages/{stage_key}/result` | 更新阶段摘要/状态 |
| POST | `/api/tasks/{task_id}/advance-substage` | 推进子阶段 |
| GET | `/api/tasks/{task_id}/substage-skills` | 获取子阶段 Skill 列表 |


### `confirm-plan` request body

Research-start drafts require all four boolean confirmations to be `true`. When the draft lists missing materials, set `missingMaterialsDecision` to `accept_limitations` to proceed; the other decisions keep the draft blocked for follow-up.

```json
{
  "researchGoalConfirmed": true,
  "existingMaterialsConfirmed": true,
  "missingMaterialsDecision": "accept_limitations",
  "nonCitableBoundariesAccepted": true,
  "nextStepLimitationsConfirmed": true,
  "acceptedLimitations": ["optional limitation note"],
  "notes": "optional user note"
}
```

Use the same body with `POST /api/tasks/{task_id}/research-start-decision` when the user chooses `supplement`, `modify_goal`, or `pause` instead of accepting limitations. The task remains a draft and records the decision in the research-start artifact.

---

## 模块

| 路径 | 职责 |
|------|------|
| `app/config.py` | 环境变量配置（所有 runtime 设置） |
| `app/main.py` | FastAPI 应用、中间件、结构化日志、启动生命周期 |
| `app/auth.py` | Bearer Token 认证与请求身份解析 |
| `app/routes/health.py` | 健康检查与诊断端点 |
| `app/routes/tasks.py` | 任务 CRUD + Artifact 访问 |
| `app/routes/agent.py` | Agent 对话、流式、Skill 执行 |
| `app/modules/task_store.py` | 运行时内存任务库 + 持久化桥接 |
| `app/modules/task_repository.py` | PostgreSQL 仓储层 |
| `app/modules/task_dev_seed.py` | 可选 dev fixture 灌库 |
| `app/modules/artifact_content.py` | Artifact 内容解析与存储分层 |
| `app/models/schemas.py` | Pydantic 与 `packages/shared` 对齐 |
| `app/execution/agent_service.py` | Agent 编排（会话、消息、状态） |
| `app/execution/claude_code_runner.py` | Claude Code CLI 执行 |
| `app/execution/runtime_boundary.py` | API vs Runtime 边界抽象 |

---

## 相关文档

- 父级 spec：`docs/task_specs/openair_phase1_cloud_prototype.md`
- 实施清单：`docs/task_specs/openair_phase1_cloud_prototype_checklist.md`
- Cloud-07 spec：`docs/task_specs/cloud_phase1/Cloud-07_deploy_observability_cost_control.md`
- Sprint01 后端任务：`docs/task_specs/sprint01/S01-05-20_backend_tasks_api.md`
- Fixture 规则：`fixtures/fixture_rules.md`

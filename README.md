# OpenAIR

> 面向材料干湿实验的科研智能体平台：把「问题建模 — 方案设计 — 执行验证 — 洞察生成」的科研过程，建模为**可编排、可验证、可追溯**的流水线。
>
> A research-agent platform that orchestrates the full research lifecycle — problem modeling, solution design, execution & validation, and insight generation — with traceable evidence.

[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](LICENSE)
[![CI](https://github.com/pdl-openair/OpenAIR/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/pdl-openair/OpenAIR/actions/workflows/oss-ci.yml)
[![Release](https://img.shields.io/github/v/release/pdl-openair/OpenAIR)](https://github.com/pdl-openair/OpenAIR/releases)

## 为什么是 OpenAIR

科研任务的痛点：周期长、工具分散、证据难追溯、实验反馈利用不足。OpenAIR 的做法：

- **四阶段过程模型**组织整个科研活动（是组织框架而非固定串行流水线，出现新计算结果或实验反馈时可回到相关阶段补充分析）；
- 能力由 **200+ Markdown 形态的 skill** 承载（SKILL.md + input/output schema），registry 统一注册、可独立测试与回放；
- 科学证据**可追溯决策**：每步产物的来源链可回看、可核验；
- 长程任务持续执行：干实验（计算验证）与湿实验（制备反馈）协同闭环。

两个已验证场景：

| 场景 | 结果 |
|---|---|
| 晶体材料发现（候选生成 → 进化搜索 → 计算验证） | 搜索终评 mSUN 达 **58.0%** |
| AlN 薄膜 PVD 制备参数寻优（四轮湿实验反馈迭代） | 最大压电性能 d33 从 15.9 提升至 **18.6** |

## 架构

```text
┌─────────────┐   ┌────────────────────────────────────────────┐
│  apps/web   │   │  services/api（FastAPI 编排运行时）            │
│  React 工作台 │──▶│  四阶段编排 · 任务/产物/证据链 · skill 调度     │
└─────────────┘   └──────────────┬─────────────────────────────┘
                                 │ SKILL_EXEC_MODE = live │ replay
                  ┌──────────────▼──────────┐   ┌──────────────────┐
                  │ skills/ + schemas/       │   │ services/worker   │
                  │ 200+ 科研 skill · 注册表  │   │ packages/llm_core │
                  └─────────────────────────┘   └──────────────────┘
```

- **工作台**（`apps/web`）：自然语言发起任务，跟踪四阶段进度、产物与证据链回看
- **编排运行时**（`services/api`）：四阶段生命周期编排与持久化，产物来源链可追溯
- **skill 层**（`skills/` + `schemas/`）：Markdown 形态能力契约（SKILL.md + input/output schema），registry 统一注册；`SKILL_EXEC_MODE=live` 走真实 Agent 执行，`replay` 用 fixtures 回放（免 key 演示与测试）
- **契约与适配层**（`packages/`）：`@openair/shared` 类型契约 / `llm_core` 供应商中立的 LLM 传输 / MCP·skill 适配器

## 部署与快速开始（Docker）

前置：Docker 24+（Docker Desktop 或 Engine，含 compose v2）。

```bash
git clone https://github.com/pdl-openair/OpenAIR.git
cd OpenAIR
cp .env.example .env     # 按下文填写/按需修改
docker compose up -d --build
```

启动后的服务入口：

| 服务 | 地址 |
|---|---|
| 工作台（Web） | http://localhost:8080 |
| API 健康检查 | http://localhost:8000/health |
| MinIO 控制台 | http://localhost:9001 |
| PostgreSQL / Redis | localhost:15432 / localhost:16379（仅本机调试用） |

修改 `.env` 后执行 `docker compose up -d` 重启即可生效（compose 会按差异重建对应服务）。

### 第一步：登录

compose 以生产口径启动（强制登录）。在 `.env` 中配置启动管理员，否则无法登录：

```bash
OPENAIR_BOOTSTRAP_ADMIN_EMAIL=you@example.com    # 管理员邮箱
OPENAIR_BOOTSTRAP_ADMIN_PASSWORD=your-password   # 至少 8 位
OPENAIR_BOOTSTRAP_ADMIN_USERNAME=ops             # 用户名（默认 ops）
```

重启后用该账号登录工作台。之后也可在前端注册普通账号（图形验证码默认关闭）。

### 第二步：配置 `.env`

`.env.example` 复制为 `.env` 后，以下 **4 项是 compose 启动的硬性要求**（模板已带本地默认值，开箱能跑；**对外部署务必全部换成随机值**，如 `openssl rand -hex 32`）：

| 变量 | 用途 | 模板默认值 |
|---|---|---|
| `POSTGRES_PASSWORD` | 数据库密码（API 与 worker 必须一致） | `openair-local-dev-password` |
| `OPENAIR_MINIO_ROOT_PASSWORD` | MinIO 密码 | `openair-local-dev-password` |
| `OPENAIR_JWT_SECRET_KEY` | 登录 JWT 签名密钥 | 占位串（必须替换） |
| `OPENAIR_WORKER_AUTH_TOKEN` | API 与 worker 互认口令 | `openair-local-worker-token` |

其余按需分组（完整逐项注释见 `.env.example`，此处只列关键项）：

| 分组 | 关键变量 | 什么时候需要 |
|---|---|---|
| **执行模式** | `SKILL_EXEC_MODE=live \| replay` | 默认 `live`（真调模型）；`replay` = 回放 fixtures，**零密钥即可体验全流程** |
| 演示数据 | `OPENAIR_DEV_SEED_FIXTURES=1` | 启动时灌入演示任务，新手体验建议开；日常保持 0 |
| **Skill/Agent 网关** | `ANTHROPIC_BASE_URL`、`ANTHROPIC_API_KEY`、`OPENAIR_CLAUDE_MODEL` | `live` 模式必填；任意 Anthropic 兼容网关（商业网关/自建均可） |
| **规划器（开题/出方案）** | `LLM_PROVIDER_BASE_URL`、`LLM_PROVIDER_API_KEY`、`LLM_PROVIDER_MODEL` | `live` 模式必填；OpenAI 兼容端点（注意：不要填 `…/api/anthropic` 路径，会 404） |
| 文献检索 | `OT_DEEPXIV_TOKEN`、`SEMANTIC_SCHOLAR_API_KEY` | 用到文献类 skill 才填 |
| 材料 MCP | `MATERIALS_MCP_BASE_URL`、`MP_API_KEY` | 晶体设计链 live 执行时填 |
| 超算（JCC） | `JCC_BASE_URL / JCC_USERNAME / JCC_PASSWORD / JCC_CLUSTER_NAME / JCC_CLI_PATH` | 接入超算才填；`jcc` CLI 不随仓库分发二进制，请自行安装后用 `JCC_CLI_PATH` 指向 |
| 云实验平台 | `FLUX_BASE_URL`、`FLUX_API_KEY` | 需**机构 VPN** 与平台账号；不配置时资源广场自动展示本地演示快照 |

### 零密钥回放演示（推荐新用户第一步）

不配置任何模型密钥，先体验平台全流程：

```bash
# .env 中设置：
SKILL_EXEC_MODE=replay
OPENAIR_DEV_SEED_FIXTURES=1
# 然后重启
docker compose up -d
```

任务执行将走仓库内 fixtures 回放（不发起任何真实 LLM 调用），可完整查看四阶段进度、产物与证据链。

### 手动部署（无 Docker）

前端（Node 20+ / pnpm 9）：

```bash
pnpm install
pnpm --filter @openair/web dev
```

后端（Python 3.12+，依赖用 [uv](https://docs.astral.sh/uv/) 管理；`.env` 同样放在仓库根目录）：

```bash
cd services/api
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# → http://localhost:8000/health 返回 {"status":"ok"}
```

Windows 注意：`uv sync` 报 SSL 错误时，用已签名 Python 重建 venv（`uv venv --python <python.exe 绝对路径>`）。更多细节见 [docs/03_工程/待专项更新/LOCAL_DEVELOPMENT.md](docs/03_工程/待专项更新/LOCAL_DEVELOPMENT.md)。

## 四阶段流水线

**problem_modeling（问题建模）→ solution_design（解法设计）→ execution_validation（执行验证）→ insight_generation（洞察生成）**

四阶段是科研活动的组织框架，而非必须依次完整执行的固定流水线：当研究过程中出现新的计算结果或实验反馈时，平台可依据当前科研状态重新进入相关阶段，开展必要的补充分析和后续决策。

## Skills 与 Benchmark

- skill 规范与注册表：`skills/`、`schemas/`（含 `_template` 起手模板）
- 评测资产：`evals/`（效果评测 + 产品 harness）、`verification/suites/`（契约测试）、`fixtures/`（回放数据）

## Roadmap

- **v0.2**：materials 域 skill 全量打磨；benchmark 公开对比表与一键复现脚本；公开仓 python lint 棘轮与后端测试
- **v0.3**：`openair` CLI（init / run / replay）；国产算力适配验证（MXMACA / 昇腾）
- **v1.0**：多用户与生产部署指南

## 贡献与许可

- 贡献指南见 [CONTRIBUTING.md](CONTRIBUTING.md)
- 本项目以 [Apache-2.0](LICENSE) 许可发布

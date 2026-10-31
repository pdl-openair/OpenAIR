<p align="center">
  <img src="apps/web/public/brand/openair-logo.png" alt="OpenAIR" width="200"/>
</p>

<h3 align="center">面向材料干湿实验的科研智能体平台</h3>
<p align="center">
  把「问题建模 — 方案设计 — 执行验证 — 洞察生成」的科研过程，建模为<b>可编排、可验证、可追溯</b>的流水线
</p>

<p align="center">
  <a href="https://github.com/pdl-openair/OpenAIR/releases"><img alt="Release" src="https://img.shields.io/github/v/release/pdl-openair/OpenAIR?color=blue"></a>
  <a href="https://github.com/pdl-openair/OpenAIR/actions/workflows/oss-ci.yml"><img alt="CI" src="https://github.com/pdl-openair/OpenAIR/actions/workflows/oss-ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-blue"></a>
  <a href="https://github.com/pdl-openair/OpenAIR/releases/download/v0.1.0/openair-demo-v0.1.0.mp4">演示视频</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a>
</p>

<p align="center"><b>简体中文</b> | <a href="README.en.md">English</a></p>

<p align="center">
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-特性">特性</a> ·
  <a href="#-架构">架构</a> ·
  <a href="#-已验证场景">已验证场景</a> ·
  <a href="#-部署指南">部署指南</a> ·
  <a href="#️-roadmap">Roadmap</a> ·
  <a href="#-贡献">贡献</a>
</p>

---

## 🗞️ News

- **2026-10-29**　🚀 v0.1.0 首个公开版本发布：四阶段编排运行时、200+ 科研 skill、评测与回放资产、双执行模式（live / replay）。[Release Notes](https://github.com/pdl-openair/OpenAIR/releases/tag/v0.1.0)

## 📖 概述

科研任务的痛点是**周期长、工具分散、证据难追溯、实验反馈利用不足**。OpenAIR 将科研过程建模为四阶段生命周期，由平台统一编排，AI 在每个阶段以「skill」形式提供能力，人始终可以在工作台上确认方案、跟踪进度、核验证据。

**Highlights**

- 🔁 **四阶段过程模型** —— problem_modeling → solution_design → execution_validation → insight_generation；是组织框架而非固定串行流水线，新的计算结果或实验反馈可触发回到相关阶段补充分析
- 🧩 **200+ Markdown 形态 skill** —— SKILL.md + input/output schema 契约化，registry 统一注册，可独立测试与回放
- 🔍 **证据可追溯** —— 每步产物的来源链可回看、可核验，形成可信闭环
- 🔬 **干湿实验协同** —— 计算验证（干实验）与制备反馈（湿实验）在同一流水线内闭环迭代
- 🎭 **双执行模式** —— `SKILL_EXEC_MODE=live` 真实调用模型网关；`replay` 回放 fixtures，**零密钥跑通全流程**

## ✨ 特性

| 能力 | 说明 |
|---|---|
| 自然语言任务工作台 | 从一句话研究意图出发，确认方案、跟踪四阶段进度、浏览产物 |
| Skill 注册与调度 | registry + skill-bindings 声明式注册，按 substage 自动编排 |
| 产物与证据链 | 任务产物一等公民，来源链（哪个 skill、什么输入、什么版本）全程可追溯 |
| 评测体系 | 效果评测（`evals/`）+ 契约测试（`verification/suites/`）+ 回放数据（`fixtures/`） |
| 平台接入 | 超算（JCC）、云实验平台、材料 MCP、文献检索，全部环境变量配置，不绑定供应商 |
| 多用户与配额 | JWT 登录、bootstrap 管理员、用户额度（trial / free / pro / internal） |

## 🏗️ 架构

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

- **工作台**（`apps/web`）：React 18 + Ant Design，任务看板 / 详情 / 产物库 / 资源广场
- **编排运行时**（`services/api`）：FastAPI，四阶段生命周期编排、PostgreSQL 持久化、durable worker 派发
- **skill 层**（`skills/` + `schemas/`）：12 个领域包（materials / opentrajectory / pvd / cs_research …），`_template` 起手模板
- **契约与适配层**（`packages/`）：`@openair/shared` 类型契约 / `llm_core` 供应商中立 LLM 传输 / MCP · skill 适配器

## 🔬 已验证场景

| 场景 | 流程 | 结果 |
|---|---|---|
| **晶体材料发现** | 候选生成 → 进化搜索 → 计算验证（VASP 能带/稳定性） | 搜索终评 **mSUN 58.0%** |
| **AlN 薄膜 PVD 参数寻优** | 文献证据 → 参数推荐 → 湿实验制备 → 反馈迭代 | 四轮反馈，d33 从 15.9 提升至 **18.6** |

**👀 在线演示（免部署、免登录）**：[Mg-O 宽带隙半导体筛选 · 完整四阶段工作台快照](https://pdl-openair.github.io/workbench/task_25047b1d994e4b6aa91578d595d75425)——真实前端 + 请求级回放，可查看阶段进度、全部产物与证据链（只读）。

![OpenAIR 演示](assets/demo-overview.gif)

🎬 完整 45 秒演示视频（含讲解）：[openair-demo-v0.1.0.mp4](https://github.com/pdl-openair/OpenAIR/releases/download/v0.1.0/openair-demo-v0.1.0.mp4)

<details>
<summary><b>复现上述场景所需的外部账号</b></summary>

| 能力 | 环境变量 | 说明 |
|---|---|---|
| 模型网关（Skill 执行 + 开题规划） | `ANTHROPIC_*` / `LLM_PROVIDER_*` | 任意 Anthropic / OpenAI 兼容网关 |
| Materials Project | `MP_API_KEY` | **免费申请**：https://next-gen.materialsproject.org/api |
| 材料 MCP 服务 | `MATERIALS_MCP_BASE_URL` | 自建或对接现有 MCP 端点 |
| 超算 DFT（VASP） | `JCC_*` | 有超算账号时配置 |
| 云实验平台 | `FLUX_BASE_URL` / `FLUX_API_KEY` | 需机构 VPN 与平台账号 |

不配置以上账号时：平台本身（登录、建任务、看板、产物库）与 replay 回放完全可用，涉及外部数据/算力的节点会如实报告 `partial_failure`。

</details>

---

## 🚀 快速开始

三种体验路径，按投入递增：

| 路径 | 需要什么 | 得到什么 |
|---|---|---|
| **A. 在线演示** | 只需浏览器 | [完整四阶段工作台快照](https://pdl-openair.github.io/workbench/task_25047b1d994e4b6aa91578d595d75425)（免登录、只读） |
| **B. 本地零密钥** | Docker + 约 10 分钟 | 自部署平台 + replay 回放演示（见下方[零密钥回放演示](#零密钥回放演示推荐新用户第一步)） |
| **C. 完整能力** | B + 模型网关 / 外部账号 | live 模式真实科研流水线（所需账号见[已验证场景](#-已验证场景)的外部账号表） |

### 前置

- Docker 24+（Docker Desktop 或 Engine，含 compose v2）；或手动部署见[部署指南](#-部署指南)

<details>
<summary><b>方式一：openair CLI 一键启动（推荐新用户）</b></summary>

```bash
pipx install openair-cli        # 或 pip install --user openair-cli
openair up                      # 克隆 → 生成 .env（安全项随机化）→ 启动全栈 → 打开浏览器
openair up --mode replay        # 零密钥回放演示，推荐第一次这样跑
```

首次运行自动生成并**仅打印一次**登录账号（同时写入 `.env`）；本地开发安装：`uv tool install --force ./cli`。其他命令：`openair status / seed / down`（详见 [cli/README.md](cli/README.md)）。

</details>

### 方式二：手动三步

### 1. 克隆并配置

```bash
git clone https://github.com/pdl-openair/OpenAIR.git
cd OpenAIR
cp .env.example .env
```

`.env` 中 compose 硬性要求的 **4 个变量**（模板已带本地默认值，开箱能跑）：

> [!NOTE]
> `.env.example` 的所有注释均为独立行，可放心整份复制——行内注释（`KEY= # 说明`）在部分 Compose 版本会被并入变量值，本仓库已从模板侧规避该坑。

| 变量 | 用途 |
|---|---|
| `POSTGRES_PASSWORD` | 数据库密码（API 与 worker 必须一致） |
| `OPENAIR_MINIO_ROOT_PASSWORD` | MinIO 密码 |
| `OPENAIR_JWT_SECRET_KEY` | 登录 JWT 签名密钥 |
| `OPENAIR_WORKER_AUTH_TOKEN` | API 与 worker 互认口令 |

> [!IMPORTANT]
> 对外部署时，以上 4 项务必全部换成随机值（`openssl rand -hex 32`）。

**登录账号**（compose 以生产口径强制登录，必配）：

```bash
OPENAIR_BOOTSTRAP_ADMIN_EMAIL=you@example.com    # 管理员邮箱
OPENAIR_BOOTSTRAP_ADMIN_PASSWORD=your-password   # 至少 8 位
OPENAIR_BOOTSTRAP_ADMIN_USERNAME=ops             # 默认 ops
```

### 2. 启动

```bash
docker compose up -d --build     # 首次构建约几分钟；改 .env 后重跑 docker compose up -d 生效
```

> 🇨🇳 国内网络拉取镜像慢/失败？先看[部署指南 · 国内网络环境配置](#-部署指南)。

### 3. 验证

| 服务 | 地址 |
|---|---|
| 工作台（Web） | http://localhost:8080 |
| API 健康检查 | http://localhost:8000/health |
| MinIO 控制台 | http://localhost:9001 |

用 bootstrap 管理员账号登录工作台，即可发起第一个任务。

#### 零密钥回放演示（推荐新用户第一步）

`.env` 中设置 `SKILL_EXEC_MODE=replay` 后重启（`docker compose up -d`）：启动不再校验模型网关，skill 执行走仓库内 fixtures 回放，**不需要任何模型密钥**。

```bash
# .env 中设置：
SKILL_EXEC_MODE=replay
```

当前边界（如实说明）：

- **材料全流程**为确定性编排（不依赖 LLM 规划器），可直接发起；其中「MP 候选检索」等外部数据节点未配 `MP_API_KEY` 时会如实报告 `partial_failure`（免费申请见上方外部账号表）
- **通用研究任务**的开题/出方案由规划器（`LLM_PROVIDER_*`）驱动，replay 模式暂不覆盖该环节——需要真实网关密钥
- `OPENAIR_DEV_SEED_FIXTURES=1` 演示种子当前仅在 `json` 存储后端生效（compose 默认 postgres 下不生效）；想直接看完整效果，用上面的**在线演示快照**

## 📦 部署指南

<details>
<summary><b>国内网络环境配置（Docker 镜像加速）</b></summary>

`docker compose up` 拉取镜像缓慢或失败（中国大陆网络常见），先为 Docker 配置镜像加速：

- **Docker Desktop**：Settings → Docker Engine，编辑 JSON 后 Apply & Restart
- **Linux**：编辑 `/etc/docker/daemon.json`，然后 `sudo systemctl restart docker`

```json
{
  "builder": { "gc": { "defaultKeepStorage": "20GB", "enabled": true } },
  "registry-mirrors": [
    "https://docker.1panel.live",
    "https://hub.rat.dev",
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ]
}
```

> 镜像加速地址时效性强，若全部失效，请搜索「docker 镜像加速 可用」更换最新地址。

手动部署路径的依赖换源：

```bash
pnpm config set registry https://registry.npmmirror.com            # npm 源
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple   # PyPI 源（uv）
```

</details>

<details>
<summary><b>按需配置分组</b>（完整逐项注释见 <code>.env.example</code>）</summary>

| 分组 | 关键变量 | 什么时候需要 |
|---|---|---|
| 执行模式 | `SKILL_EXEC_MODE=live \| replay` | 默认 `live`；`replay` 零密钥体验全流程 |
| 演示数据 | `OPENAIR_DEV_SEED_FIXTURES=1` | 新手体验建议开；日常保持 0 |
| Skill/Agent 网关 | `ANTHROPIC_BASE_URL`、`ANTHROPIC_API_KEY`、`OPENAIR_CLAUDE_MODEL` | `live` 模式必填；任意 Anthropic 兼容网关 |
| 规划器（开题/出方案） | `LLM_PROVIDER_BASE_URL`、`LLM_PROVIDER_API_KEY`、`LLM_PROVIDER_MODEL` | `live` 模式必填；OpenAI 兼容端点（勿填 `…/api/anthropic` 路径，会 404） |
| 文献检索 | `OT_DEEPXIV_TOKEN`、`SEMANTIC_SCHOLAR_API_KEY` | 用到文献类 skill 才填 |
| 材料 MCP | `MATERIALS_MCP_BASE_URL`、`MP_API_KEY` | 晶体设计链 live 执行时填 |
| 超算（JCC） | `JCC_BASE_URL / JCC_USERNAME / JCC_PASSWORD / JCC_CLUSTER_NAME / JCC_CLI_PATH` | 接入超算才填；`jcc` CLI 不随仓库分发，自行安装后经 `JCC_CLI_PATH` 指向 |
| 云实验平台 | `FLUX_BASE_URL`、`FLUX_API_KEY` | 需机构 VPN 与平台账号；不配置时资源广场自动展示本地演示快照 |

</details>

<details>
<summary><b>服务与端口</b></summary>

| 服务 | 端口 | 说明 |
|---|---|---|
| web | 8080（`OPENAIR_WEB_PORT` 可改） | nginx 同源反代到 api |
| api | 8000 | FastAPI |
| postgres | 15432（宿主机） | 数据库 |
| minio | 9000 / 9001 | S3 / 控制台 |
| redis | 16379 | 队列 |

</details>

<details>
<summary><b>手动部署（无 Docker）</b></summary>

前端（Node 20+ / pnpm 9）：

```bash
pnpm install
pnpm --filter @openair/web dev
```

后端（Python 3.12+，[uv](https://docs.astral.sh/uv/) 管理依赖；`.env` 放仓库根目录）：

```bash
cd services/api
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# → http://localhost:8000/health 返回 {"status":"ok"}
```

Windows：`uv sync` 报 SSL 错误时用已签名 Python 重建 venv（`uv venv --python <python.exe 绝对路径>`）。详见 [本地开发文档](docs/03_工程/待专项更新/LOCAL_DEVELOPMENT.md)。

</details>

## 🗺️ Roadmap

| 版本 | 内容 |
|---|---|
| **v0.2** | materials 域 skill 全量打磨；benchmark 公开对比表与一键复现脚本；公开仓 python lint 棘轮与后端测试 |
| **v0.3** | `openair` CLI 增强（初版 `up/status/seed/down` 已随 v0.1.x 交付，见 [cli/](cli/)）；国产算力适配验证（MXMACA / 昇腾） |
| **v1.0** | 多用户与生产部署指南 |

## 🤝 贡献

欢迎 Issue 与 PR！开发环境、提交规范与 PR 要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

- 严禁提交任何真实密钥 / 内网地址 / 个人隐私（CI 有 gitleaks 全量扫描）
- 新增或修改 skill 需同步 `skills/registry.yaml` 与对应 schema，并保证 replay 模式可回放

## 📄 License

本项目以 [Apache-2.0](LICENSE) 许可发布。第三方组件（如外部来源 skill）遵循各自声明。

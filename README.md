# OpenAIR

> 面向材料干湿实验的科研智能体平台：把「问题建模 — 方案设计 — 执行验证 — 洞察生成」的科研过程，建模为**可编排、可验证、可追溯**的流水线。
>
> A research-agent platform that orchestrates the full research lifecycle — problem modeling, solution design, execution & validation, and insight generation — with traceable evidence.

[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](LICENSE)
[![CI](https://github.com/Maxine-1520/OpenAIR/actions/workflows/oss-ci.yml/badge.svg)](https://github.com/Maxine-1520/OpenAIR/actions/workflows/oss-ci.yml)
[![Release](https://img.shields.io/github/v/release/Maxine-1520/OpenAIR)](https://github.com/Maxine-1520/OpenAIR/releases)

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

## 5 分钟 Quickstart（replay 模式，无需任何 API key）

```bash
git clone https://github.com/Maxine-1520/OpenAIR.git
cd OpenAIR
docker compose up -d --build      # 首次构建约几分钟
# 工作台:      http://localhost:8080
# API 健康检查: http://localhost:8000/health
```

- 开箱即回放：样例任务回放不依赖任何外部服务与密钥（`SKILL_EXEC_MODE=replay`）
- 接入真实模型网关 / 超算 / 云实验平台：全部经环境变量配置，见 `.env.example`（架构不绑定任何供应商）

<details><summary>无 Docker 的手动路径（Windows / macOS / Linux）</summary>

```bash
pnpm install && pnpm --filter @openair/web dev          # 前端
cd services/api && uv sync && uv run uvicorn app.main:app --reload   # 后端
```

</details>

## Skills 与 Benchmark

- skill 规范与注册表：`skills/`、`schemas/`（含 `_template` 起手模板）
- 评测资产：`evals/`（效果评测 + 产品 harness）、`verification/suites/`（契约测试）、`fixtures/`（回放数据）

<!-- TODO(v0.2)：benchmark 公开对比表（跑批后回填） -->

## 平台接入（可选）

- 超算/云算力（JCC）与云实验平台经环境变量接入，见 `.env.example` 的 `JCC_*` / `FLUX_*` 段；
- 云实验平台需**机构 VPN 与平台账号**；未配置时，资源广场自动展示本地演示快照。

## Roadmap

- **v0.2**：materials 域 skill 全量打磨；benchmark 公开结果表与一键复现脚本
- **v0.3**：`openair` CLI（init / run / replay）；国产算力适配验证（MXMACA / 昇腾）
- **v1.0**：多用户与生产部署指南

## 贡献与许可

- 贡献指南见 [CONTRIBUTING.md](CONTRIBUTING.md)
- 本项目以 [Apache-2.0](LICENSE) 许可发布

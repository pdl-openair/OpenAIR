# 贡献指南

感谢关注 OpenAIR！

## 开发环境

- Node 20+ / pnpm 9+；后端 Python 3.12+（依赖用 [uv](https://docs.astral.sh/uv/) 管理）
- 前端：`pnpm install && pnpm --filter @openair/web dev`
- 后端：`cd services/api && uv sync && uv run uvicorn app.main:app --reload`
- 常用门禁：`ruff check`（Python lint）、`pnpm --filter @openair/web build`（前端构建）、`pytest`（后端测试）

## 分支与提交

- 从 `main` 拉取分支：`feat/xxx` / `fix/xxx` / `docs/xxx`
- 提交信息格式：`type(scope): 摘要`，type 取 `feat / fix / docs / refactor / test / chore`

## PR 要求

- 说明：改了什么、如何验证、影响范围与风险
- 新增或修改 skill 时，需同步 `skills/registry.yaml` 与对应 schema，并保证 `SKILL_EXEC_MODE=replay` 下可回放
- 不引入新的外部服务强依赖；平台接入一律走环境变量（见 `.env.example`）

## 安全

- 严禁提交任何真实密钥、内网地址、个人隐私信息；CI 配有 gitleaks 全量扫描
- 演示/回放数据使用脱敏样例，不使用真实实验账号

## 许可

提交 PR 即表示你同意以 [Apache-2.0](LICENSE) 许可发布你的贡献。

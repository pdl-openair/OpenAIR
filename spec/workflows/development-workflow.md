# 本地开发与提交流程（development-workflow）

> 本地开发 → 提交 → PR 的主流程。三层本地强制的分工：**hooks=Claude Code 专属加速；pre-commit=IDE 无关本地层；CI=唯一权威**（同一规则三层表达，实现同源，见 [../governance/ai-collaboration.md](../governance/ai-collaboration.md)）。

## 职责

承载"环境怎么起、命令怎么跑、代码放哪、怎么提交"的操作事实与流程规则。

## 适用场景

新人起环境；日常开发与提交；决定新文件/新代码放哪个目录。

## 环境要点（Windows + Git Bash）

- 前端：pnpm workspace（`pnpm install` 后 `pnpm --filter @openair/web dev` 起开发服 :5173；`build` 为生产构建）。
- 后端：`services/api/` 下建 venv（`uv sync` 或 `pip install -r requirements-local.txt`，后者含 `-e ../../packages/llm_core`，`import llm_core` 是导入期硬依赖）；`uvicorn app.main:app --reload --port 8000 --timeout-graceful-shutdown 10`（限制 SSE 长连接在热重载时的退出等待）；健康检查 `GET /health` → `{"status":"ok"}`。
- **不要让 uv 下载 Python**（Smart App Control 拦未签名二进制）：用已签名解释器（miniconda/python.org）重建 venv（`uv venv --python <绝对路径>`）。
- Skill 同步：`python scripts/sync_claude_skills.py`（skills/ → .claude/skills/，生成物勿手改）；开发 skill 分发 `python docs/dev_skills/sync_dev_skills.py`。

## 哪里放新代码

| 添加什么 | 位置 |
|---|---|
| 页面/页面组件 | `apps/web/src/pages/` |
| 共享 UI 组件 | `apps/web/src/components/` |
| API client 调用 | `apps/web/src/api/` |
| 共享类型/枚举 | `packages/shared/src/` |
| 后端路由 | `services/api/app/routes/` |
| 后端模型/schema | `services/api/app/models/` |
| 执行逻辑 | `services/api/app/execution/` |
| Skill 定义 | `skills/<domain>/<skill_id>/`（登记进 `skills/registry.yaml`） |
| 流程样本/fixture | `fixtures/`（规则见 `fixtures/fixture_rules.md`） |
| 任务规格 | `docs/task_specs/` |

测试资产放置不在此表——按《测试代码架构》（`../testing/OpenAIR 测试代码架构.md`）的归属表执行。

## 提交流程

1. 复杂任务先有 task spec（`docs/task_specs/`）；fix 类触产品代码须关联 `status/verified` issue（SPEC-PROC-02，见 [issue-workflow.md](issue-workflow.md)）。
2. 原子提交：一个提交一个关注点；显式路径 `git add <paths>`，**禁止 `git add -A` / `git add .`**。
3. 提交信息 `<type>(<scope>): <summary>`，AI 参与必带 Co-authored-by——全文见 [../collaboration/git_commit_rules.md](../collaboration/git_commit_rules.md)。
4. 分支命名 `<type>/<short-task-name>`。
5. PR 前自跑本地门禁（pre-commit 镜像）；PR 证据合同字段见 [pr-workflow.md](pr-workflow.md)。

## 必须

- 字段/schema 变更四处同步（shared/Pydantic/fixtures/路由）。〔L4 checklist〕
- 修改 `skills/`、`docs/dev_skills/` 后跑对应同步脚本再提交。

## 禁止

- 手改任何生成物目录（`.claude/skills/` 等，SPEC-GEN-01）。
- `--no-verify` 跳过钩子、force push 到共享分支（SPEC-GIT-01）。
- 未经 task spec 直接让 AI coding 动复杂任务。

## 防什么失效模式

- 环境文档腐化导致新人卡在起点 → 本文件承载命令速查并由指针检查保活。
- "改了源忘了同步生成物" → 同步点写入流程 + hook 拦截手改生成物。

## 同步点

- 命令/目录变化 → 本文件 + AGENTS.md 指路 + 相关 dev skill 同步。

## 不负责什么

- 门禁工具配置 → [../foundations/coding-standards.md](../foundations/coding-standards.md)
- PR 字段与合并门禁 → [pr-workflow.md](pr-workflow.md)
- 施工事务性（先验证后写入、二次 no-op）→ [../collaboration/09_工程施工与脚本交付规范.md](../collaboration/09_工程施工与脚本交付规范.md)

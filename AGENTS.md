# AGENTS.md — AI 入口导航

OpenAIR：通用 AI 科研平台。用户用自然语言描述研究任务，平台经 4 阶段生命周期（problem_modeling → solution_design → execution_validation → insight_generation）编排，由 Claude Agent SDK 调用 skills 执行。文档以简体中文为主。

**工程规则唯一源是 [spec/README.md](spec/README.md)——本文件不是规则源，只做导航。**

## 技术栈

- pnpm workspace monorepo：前端 `apps/web/`（React 18 + Ant Design v6 + Vite）；后端 `services/api/`（FastAPI）；共享类型 `packages/shared/`；skill 唯一人维护源 `skills/`
- 后端执行多 runner 由 `SKILL_EXEC_MODE` 控制（live=Agent SDK / replay=fixture 回放）
- 各 IDE 的 skill 镜像目录（如 .claude/skills/）为生成物，禁手改（SPEC-GEN-01）

## 本地环境要点

- Windows + Git Bash；中文路径必须加引号
- 后端 Python venv 位于 services/api 下（.venv）；worktree 内无 venv 时用绝对路径调主仓解释器
- `import llm_core` 在 API import 时必需（packages/llm_core 需安装）
- 具体命令与新代码放置位见 [spec/workflows/development-workflow.md](spec/workflows/development-workflow.md)

## 指路表

| 要做什么 | 去哪 |
|---|---|
| 查任何工程规则（编码/门禁/安全/流程/测试/协作） | [spec/README.md](spec/README.md)（按任务→读哪篇矩阵） |
| 写代码前的质量底线与豁免棘轮 | [spec/foundations/coding-standards.md](spec/foundations/coding-standards.md) |
| 发 PR / 证据合同必填字段 | [spec/workflows/pr-workflow.md](spec/workflows/pr-workflow.md) |
| 写 commit message | [spec/collaboration/git_commit_rules.md](spec/collaboration/git_commit_rules.md) |
| 提交前跑门禁脚本 | [scripts/gates/](scripts/gates/) |
| 写任务规格（复杂任务先写 spec 再动码） | [docs/task_specs/TEMPLATE.md](docs/task_specs/TEMPLATE.md) |
| 产品设计合同（产品如何构成） | [docs/01_核心设计/](docs/01_核心设计/)（入口 docs/README.md） |
| 开发 skill（IDE 触发与索引） | [docs/dev_skills/](docs/dev_skills/) |

## 维护纪律

本文件保持 <60 行：加规则去 spec/ 开 PR，不要在这里写正文；路径变更由指针完整性检查（SPEC-PTR-01）保活。

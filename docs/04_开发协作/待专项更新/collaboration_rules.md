# 团队协作规则

## 1. 文档作用

本文用于约束 OpenAIR 团队协作方式。

OpenAIR 是多人并行开发项目，包含前端、后端、Skill、fixtures、文档和 AI coding 协作。本文只约定团队协作规则，不替代工程规范、产品文档和 task spec。

## 2. 基本原则

- 任务要小，边界要清楚；
- Issue 只跟踪任务，不承载长上下文；
- 复杂开发任务必须有 task spec；
- PR 必须说明改了什么、没改什么、怎么验收；
- 不跨模块随意修改；
- 不把场景流程写死进产品主线；
- 文档、代码、fixtures、Skill 契约要同步维护。

## 3. 任务管理

GitHub Issue 用于记录：

- 任务标题；
- 负责人；
- 优先级；
- 所属 Sprint；
- 状态；
- 验收标准；
- 相关 task spec 链接；
- 相关 PR 链接。

Issue 不放长篇背景、完整提示词或大段产品说明。

这些内容放在：

```text
docs/task_specs/
docs/02_产品与Workflow/
docs/03_工程/
docs/dev_skills/
docs/dev_skills/dev-ai-coding/
````

## 4. 分工边界

| 类型          | 主要目录              | 说明                           |
| ----------- | ----------------- | ---------------------------- |
| 前端开发        | `apps/web`        | 四个核心页面和组件                    |
| 后端开发        | `services/api`    | 任务、状态、产物、执行记录、接口             |
| 共享契约        | `packages/shared` | 前后端共享类型和枚举                   |
| Skill 开发    | `skills/`         | Skill 源目录、说明、schema、脚本、样例    |
| Claude 运行入口 | `.claude/`        | 由项目规则和同步脚本维护                 |
| 流程样例        | `fixtures/`       | recorded runs、任务状态、产物、调用记录样例 |
| 文档规范        | `docs/`           | 产品、工程、Skill、vibe coding、协作说明 |

## 5. 文档同步要求

以下变更必须同步文档：

* 新增页面；
* 新增或修改 API；
* 新增任务状态；
* 修改数据结构；
* 新增 Skill；
* 修改 Skill 输入输出；
* 新增 fixtures；
* 调整 Claude SDK 调用方式；
* 调整仓库目录边界。

同步文档不需要写长，但必须让其他人知道怎么使用、怎么验收。

## 6. AI coding 协作

使用 Cursor、Qoder、Claude Code 时：

* 必须先明确任务边界；
* 必须说明允许修改和禁止修改的文件；
* 必须要求输出文件清单和自检结果；
* 不允许让 AI 一次性重构全仓库；
* 不允许让 AI 自行决定删除不确定用途的代码。

复杂任务必须先写 task spec。

## 7. 会议与口头确认

口头确认不能替代仓库记录。

涉及以下内容时，必须落到 Issue、task spec 或文档：

* 开发范围变化；
* 数据契约变化；
* Skill 接入方式变化；
* 页面主流程变化；
* Sprint 验收标准变化；
* PR 合并前必须知道的限制。

## 8. 不在本文维护的内容

本文不维护：

* 产品详细说明；
* 技术架构细节；
* Skill 构建规范；
* API 契约；
* 数据库设计；
* 单个任务开发提示词。

对应内容分别放在 `docs/02_产品与Workflow/`、`docs/03_工程/`、`docs/dev_skills/` 和 `docs/task_specs/`。

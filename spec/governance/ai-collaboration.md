# AI 协作治理（ai-collaboration）

> 多 IDE 并行（Claude Code / Cursor / Trae / Qoder）下的规则源分工、hooks 分层、权限姿态与自主施工合并规则。

## 职责

规定 AI 工具协作的边界与强制分工，含战役限定的自主合并条款。

## 适用场景

配置任一 AI 工具；写/改 hooks；自主施工战役编排；评审 AI 产出。

## 规则源 IDE 无关

- **spec/ 是唯一规则源**：任何 IDE 的会话入口（AGENTS.md / CLAUDE.md 导入桥 / 各 IDE 规则文件）只做导航，不承载规则正文。
- 开发 skill 单源在 `docs/dev_skills/`，由 `sync_dev_skills.py` 分发至各家镜像目录——镜像一律只读（SPEC-GEN-01）。
- 科研 skill 单源在 `skills/`，由 `sync_claude_skills.py` 分发。

## 三层本地强制分工（同一规则三层表达，实现同源）

| 层 | 载体 | 定位 |
|---|---|---|
| Claude hooks（hook_guard，B3 交付，注册于 `.claude/settings.json`） | Claude Code 专属 | **加速层**：秒级反馈，非权威 |
| pre-commit（`.pre-commit-config.yaml`，B1 交付） | IDE 无关 | 本地层：同规则镜像，未装不阻断工作 |
| CI（required-checks，B5 接线） | 仓库层 | **唯一权威**：不可跳过 |

hooks 硬拦清单（SPEC-GIT-01 / SPEC-GEN-01，细则见 `../enforcement/enforcement-matrix.md`）：`--no-verify`、`git add -A`/`git add .`、force push、手改生成物目录；软提示：staged 规模异常且无 task spec 尾注时提醒先写 spec。

## 权限姿态

- 共享姿态（`.claude/settings.json`，入仓）：**acceptEdits + 工具 allowlist**；`bypassPermissions` 常开已废除（裁决点 4 冻结）。
- 个人本地覆盖只允许在 `settings.local.json`（保持 gitignore）。
- 施工 agent：写入区外 Edit 拒绝；reviewer agent：只读。

## 自主施工合并规则（**战役限定条款**，作用域=AI4SE Harness 重构战役）

- 批次分支从重构工作分支拉出、PR 目标=重构工作分支；**自主合并权限止于重构工作分支**。
- 合回条件：CI 全绿 + 对抗 reviewer verdict accept + 风险 ≤ medium；high 风险批次 PR 留待用户。
- **最终合入 test 必经用户评审并点击合并**；战役期间 test 不接收本战役任何直接合并。
- 单任务 WIP=1；失败重试 1 次后开 issue 让道；连续 2 任务失败 orchestrator 停机等用户。
- 本条款不改变产品开发常态原则：**永不自动合并产品 PR**。

## 必须遵守

- 使用任一 AI 工具前明确允许修改/禁止修改的文件范围（写入区 allowlist）。
- AI 参与的提交带 Co-authored-by；提交信息规范见 [../collaboration/git_commit_rules.md](../collaboration/git_commit_rules.md)。
- AI 不得以缺证据的完成声明过门禁（PR 证据合同见 [../workflows/pr-workflow.md](../workflows/pr-workflow.md)）。

## 防什么失效模式

- 多 IDE 规则各自为政、漂移 → 单源 + 生成物只读 + 指针检查。
- 权限全开导致越界写入 → acceptEdits + allowlist + 写入区外拒绝。
- 自主施工失控 → 战役限定条款 + WIP=1 + 停机线。

## 同步点

- hooks 清单或权限姿态变化 → 本文件 + `.claude/settings.json` + enforcement-matrix 对应行同 PR。

## 不负责什么

- 具体 PR 字段 → [../workflows/pr-workflow.md](../workflows/pr-workflow.md)
- agent 人设（对抗 reviewer 等）→ `.claude/agents/`（B3 交付）

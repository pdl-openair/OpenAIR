---
name: dev-release
description: >-
  开发者准备发布 OpenAIR 阶段版本、写 release notes、更新 CHANGELOG/STATUS、创建
  GitHub Release、整理 milestone 或说明已知问题时触发。用户说“发 release”、
  “阶段发布”、“发布说明”、“release notes”、“CHANGELOG”、“STATUS”、“milestone
  补齐”、“今天发布一轮”时应使用本 skill。
---

# Dev: Release Management

## 使用场景

准备或复盘 OpenAIR 阶段发布时使用。

> 本地门禁提示：pre-commit 钩子经 `python scripts/gates/run_local.py` 运行，依赖 PATH `python` 装有 ruff/mypy——缺失时钩子降级为警告放行，CI 兜底强制（见 `CONTRIBUTING.md` 第 8 节）。

## 必读文档

- `docs/04_开发协作/releases/release_process.md`：发布流程、命名、Release Notes 模板、milestone 规则。
- `STATUS.md`：当前项目状态。
- `CHANGELOG.md`：历史变更时间线。
- `.github/pull_request_template.md`：PR 验证与风险字段。
- `.github/labels.yml`：当前 labels 基线。

## 工作流

1. 明确发布范围和发布类型。
2. 查看当前分支、tag、PR、Issue。
3. 确认工作区是否干净；如果不干净，说明哪些改动进入本轮。
4. 更新 `CHANGELOG.md` 和 `STATUS.md`。
5. 按 `docs/04_开发协作/releases/release_process.md` 生成 Release Notes。
6. 检查 milestone 和 labels：
   - blocker 明确；
   - follow-up 不混入本轮范围；
   - known issue 写进发布说明。
7. 跑或列出本轮必须验证。
8. 只有用户明确要求发布时，才创建 tag 和 GitHub Release。

## 输出

输出这些内容：

- Release 标题和 tag；
- Release Notes 草稿；
- 关联 PR/Issue；
- 验证结果；
- 已知问题和后续事项；
- 发布命令或 GitHub 页面操作步骤。

> CI hygiene 硬事实：PR 带 `release` 标签会触发强制校验——`STATUS.md` / `CHANGELOG.md` 必须出现在变更清单，缺一即红（SPEC-HYGIENE-01，`check_hygiene.py` 拦截）。

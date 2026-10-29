---
name: dev-issue
description: >-
  开发者需要创建或修改 GitHub Issue 时触发。当用户说"建 issue"、"修 bug"、
  "报 bug"、"file issue"、"开发任务"、"create issue"、"提 bug"、
  "新建功能需求"时使用本 skill。
  按项目 Issue 模板生成规范的 Issue 内容。
  即使用户只说"有个 bug 要提"，只要涉及 Issue 创建就应该触发。
---

# Dev: Issue Filing

## When to use

开发者需要创建 bug report 或 feature/task Issue。

## Rules source

Read before creating:

- `.github/ISSUE_TEMPLATE/bug.md` -- bug report template
- `.github/ISSUE_TEMPLATE/feature.md` -- 通用任务 / 需求模板
- `.github/ISSUE_TEMPLATE/release.md` -- 阶段发布模板
- `spec/collaboration/03_任务推进与交接.md` -- what goes in Issue vs task spec
- `docs/04_开发协作/releases/release_process.md` -- 发布 Issue、milestone、known issue、follow-up 规则

## Pre-checks

1. Determine issue type: bug, task/feature, or release.
2. Task/feature Issue：确认是否已有执行入口；复杂任务必须引用 task spec 或相关 docs。
3. Release Issue：确认 milestone、tag、Release Notes、验证记录、known issue 和 follow-up。
4. Issue 只跟踪状态、负责人、优先级、验收结果；长上下文放在 `docs/task_specs/` 或对应 docs 下。
5. 「关联」段必须写明 milestone、前置 Issue、后续 Issue；没有则写 `N/A`。

## Output

填好的 Issue 正文。复杂任务缺执行入口时，提示先补 task spec；发布任务缺 milestone 时，提示先创建或刷新 milestone。

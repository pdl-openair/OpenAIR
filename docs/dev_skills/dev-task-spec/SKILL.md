---
name: dev-task-spec
description: >-
  开发者需要编写任务说明文件时触发。当用户说"写 task spec"、"任务说明"、
  "AI coding 上下文"、"task spec template"、"准备 AI coding 任务"、
  "create task spec"时使用本 skill。
  按模板生成结构化的任务说明，供人或 AI Coding 工具使用。
  即使用户只说"这个任务需要上下文"，只要涉及 task spec 场景就应该触发。
---

# Dev: Task Spec Writing

## When to use

开发者需要为复杂任务编写 task spec（AI coding 任务说明文件）。

## Rules source

Read before writing:

- `docs/task_specs/TEMPLATE.md` -- the template to follow
- `spec/collaboration/03_任务推进与交接.md` -- when task specs are required, what they must contain

## Pre-checks

1. Check if a linked Issue already exists
2. Confirm the task is complex enough to need a task spec (simple tasks don't)
3. Gather: background, goals, non-goals, affected directories, acceptance criteria

## Output

Task spec file in `docs/task_specs/` named as `S<XX>-<YY>-short-name.md`.

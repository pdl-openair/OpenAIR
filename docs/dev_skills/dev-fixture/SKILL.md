---
name: dev-fixture
description: >-
  开发者需要添加或修改 fixture 样例时触发。当用户说"加 fixture"、"创建 fixture"、
  "recorded run"、"样例数据"、"integration test data"、"添加 skill_io"、
  "新建 recorded run"时使用本 skill。
  按 fixture 规范创建流程样例，确保字段、命名、结构合规。
  即使用户只说"加个样例"，只要涉及 fixtures 目录就应该触发。
---

# Dev: Fixture Creation

## When to use

开发者需要添加 fixture 样例（recorded run、task state、skill_io 等）。

## Rules source

Read before creating:

- `fixtures/fixture_rules.md` -- naming, structure, required files, validation rules

## Pre-checks

1. Confirm the fixture type: recorded run, task state, artifact, capability call, skill_io
2. Check naming convention: `<domain>_<scenario>_<number>`
3. Verify no secrets, tokens, or private paths
4. 如修改了 `stages.json` / `task.json` / `artifacts.json` 等核心结构的字段，同步更新 `packages/shared/src/task.ts` 类型与 `docs/task_specs/` 中对应 spec 的字段表

## Output

Fixture files following the rules. Each fixture must have a clear source and purpose -- not random data.

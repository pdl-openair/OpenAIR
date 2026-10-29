---
name: dev-dictionary
description: 开发者需要新增或修改枚举值中文映射时触发。当用户说"加字典"、"加映射"、"枚举翻译"、"label"、"中文标签"、"dictionary"、"dict"时使用本 skill。确保所有枚举值的中英文映射集中在 `packages/shared/src/i18n/` 下维护，不散落在前端组件中。即使用户只是问"这个状态怎么显示中文"，只要涉及枚举映射就应该触发。
---

# Dev: Dictionary (字典表维护)

## When to use

开发者需要新增、修改或检查枚举值的中文映射标签。

## Rules source

Read before modifying:

- `docs/03_工程/工程规范/dictionary_rules.md` -- 字典表使用规范（权威规则）
- `packages/shared/src/i18n/dict.ts` -- 字典数据（权威数据源）
- `packages/shared/src/task.ts` -- 枚举类型定义（类型源）

## Pre-checks

1. 确认枚举类型是否已在 `packages/shared/src/task.ts` 或 `dict.types.ts` 中定义
2. 确认映射值是否已在 `dict.ts` 中收录
3. 搜索 `apps/web/src/` 确认没有组件内硬编码的 `Record<XxxEnum, string>` 映射

## Workflow

1. 在 `dict.types.ts` 或 `task.ts` 中补类型定义
2. 在 `dict.ts` 中补映射常量
3. 更新 `i18n/index.ts` 和 `packages/shared/src/index.ts` 的 export
4. 在前端组件中用 `label()` / `labelUnknown()` 替代硬拼接
5. 全局搜索确认无残留散落映射
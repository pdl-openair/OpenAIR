# 字典表使用规范

## 字典位置

所有枚举值的中英文映射集中在 `packages/shared/src/i18n/`：

- `dict.types.ts` — 补充类型（如 `ConfirmationDecision`、`ArtifactType`）
- `dict.ts` — 6 个映射常量 + `label()` / `labelUnknown()` 工具函数
- `index.ts` — barrel export

## 维护规则

1. **集中维护**：所有枚举→中文映射必须写在 `dict.ts`，不得在组件或页面内散落 `Record<XxxEnum, string>` 定义
2. **类型对齐**：映射常量的 key 类型必须与 `task.ts` 或 `dict.types.ts` 中的类型定义严格对齐
3. **Export 同步**：新增映射常量或类型后，必须在 `i18n/index.ts` 和 `packages/shared/src/index.ts` 中同步导出

## 新增枚举值流程

```
补类型（task.ts 或 dict.types.ts） → 补映射（dict.ts） → 更新 export（i18n/index.ts + src/index.ts） → 替换组件硬拼接
```

## 使用方式

### 类型安全取值（封闭枚举）

```typescript
import { STATUS_LABELS, label } from "@openair/shared";

// label(map, key) — key 必须是映射的合法 key，编译时校验
<p>状态：{label(STATUS_LABELS, task.status)}</p>
```

### Fallback 取值（开放枚举）

```typescript
import { ARTIFACT_TYPE_LABELS, labelUnknown } from "@openair/shared";

// labelUnknown(map, key) — 未知值 fallback 返回英文原文，不会崩溃
[{labelUnknown(ARTIFACT_TYPE_LABELS, a.kind)}]
```

## 当前映射常量

| 常量名 | Key 类型 | 覆盖枚举 |
|--------|---------|---------|
| `STAGE_LABELS` | `TaskStage` | 四阶段主线 |
| `STATUS_LABELS` | `TaskStatus` | 任务状态 |
| `CAPABILITY_KIND_LABELS` | `CapabilityKind` | 能力类型 |
| `CAPABILITY_CALL_STATUS_LABELS` | `CapabilityCallStatus` | 能力调用状态 |
| `CONFIRMATION_DECISION_LABELS` | `ConfirmationDecision` | 确认决策 |
| `ARTIFACT_TYPE_LABELS` | `ArtifactType` | 产物类型（开放枚举） |

## Artifact.kind 开放枚举策略

`Artifact.kind` 在 `task.ts` 中是 `string` 类型，可能随 Skill 扩展产生新值。策略：

- 已知类型收录在 `ARTIFACT_TYPE_LABELS` 中
- 新增产物类型时，先在 `dict.types.ts` 的 `ArtifactType` 中补值，再在 `dict.ts` 补映射
- 未知值通过 `labelUnknown()` fallback 显示英文原文，不崩溃

## Sprint01 约束

- 不引入 i18n 框架（如 react-i18next）
- 不做 locale 切换，仅中文映射
- 不强制修改已有 fixture JSON 中的值
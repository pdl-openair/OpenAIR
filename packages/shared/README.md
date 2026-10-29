# `@openair/shared`

前后端共享的**稳定契约**（类型与常量），不放业务实现。

## 概念与源码位置

| 概念 | 说明 | 当前实现 |
|------|------|----------|
| task | 任务聚合根、状态、阶段列表 | `src/task.ts` 中 `Task` 等 |
| stage | 四阶段主线枚举 | `src/task.ts` 中 `TaskStage` |
| artifact | 过程产物 | `src/task.ts` 中 `Artifact` |
| capability_call | 能力调用记录 | `src/task.ts` 中 `CapabilityCall` |
| skill | Skill 元数据与注册以仓库 `skills/registry.yaml` 为准；本包不承载具体 Skill | — |
| dashboard | 态势看板用指标与切片类型待契约定稿后补充 | — |
| dict | 枚举值中英文映射字典 | `src/i18n/dict.ts` 中 `STAGE_LABELS` 等 6 个映射常量 |

权威字段与枚举修订流程见 `docs/03_工程/待专项更新/architecture.md` 第 7 节（共享契约层）。

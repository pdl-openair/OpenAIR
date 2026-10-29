---
name: parameter-recommendation
description: 把目标候选与实验室约束翻译为可执行的制备/合成参数方案，给出基线配方与替代方案，产出参数推荐表。
---

# Parameter Recommendation（L2 编排）

## 职责

把"目标候选 + 实验室约束"翻译为可执行的制备方案，按 spec 产出参数表（materials pack 为 `flux_parameters.md`），供下游 `synthesis-execution` 执行。

本 L2 是**域无关**编排：制备方法（Flux / 水热 / CVD / 其他）、参数项、推荐算法由 L3 实现。L2 只负责：目标候选消费、实验室约束收集、替代方案组织、参数表格式化。

## 执行步骤

1. 读取上游 `novel_candidates.md` 或 `mp_seeds.md`，选定目标
2. 调用域特定 L3（materials pack 调用 `materials-parameter-recommend-flux`）推荐制备参数
3. 与用户确认实验室约束（坩埚上限温度、气氛条件、设备能力等）
4. 按 spec（`schemas/materials/specs/parameter-recommendation.md`）产出参数表，含替代方案

## 产出

见 spec。output_file = `flux_parameters.md`（materials pack 约定）。

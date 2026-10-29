---
name: opentrajectory-validation-plan-generation
description: 为每条假说设计最小可执行验证方案，明确预测、证伪条件、风险和优先级。
---

# 最小验证方案生成

## 目标
为每条假说设计最小可执行验证方案，明确预测、证伪条件、风险和优先级。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

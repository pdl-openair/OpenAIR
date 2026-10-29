---
name: opentrajectory-constraints-criteria
description: 解释硬约束、软约束、筛选条件和检索边界，为后续设计提供明确规则。
---

# 约束与标准

## 目标
解释硬约束、软约束、筛选条件和检索边界，为后续设计提供明确规则。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

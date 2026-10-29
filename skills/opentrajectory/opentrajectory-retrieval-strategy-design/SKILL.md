---
name: opentrajectory-retrieval-strategy-design
description: 根据研究目标和约束，设计覆盖不同方向、机制和指标的检索矩阵。
---

# 检索策略设计

## 目标
根据研究目标和约束，设计覆盖不同方向、机制和指标的检索矩阵。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

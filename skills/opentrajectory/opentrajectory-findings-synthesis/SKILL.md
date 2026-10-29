---
name: opentrajectory-findings-synthesis
description: 只整理已有证据和机制链已经支持的结论，明确适用边界与不确定性，不混入新增假说。
---

# 已有结论凝练

## 目标
只整理已有证据和机制链已经支持的结论，明确适用边界与不确定性，不混入新增假说。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

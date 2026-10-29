---
name: opentrajectory-hypothesis-update
description: 将科研线索转化为尚待验证的可证伪假说，明确变量、方向、边界和不确定性。
---

# 待验证假说生成与更新

## 目标
将科研线索转化为尚待验证的可证伪假说，明确变量、方向、边界和不确定性。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

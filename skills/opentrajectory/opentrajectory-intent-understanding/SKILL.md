---
name: opentrajectory-intent-understanding
description: 将用户提出的科研问题整理为结构化需求，明确研究对象、目标、约束和预期结果。
---

# 科研需求理解

## 目标
将用户提出的科研问题整理为结构化需求，明确研究对象、目标、约束和预期结果。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

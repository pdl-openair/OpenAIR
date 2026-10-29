---
name: opentrajectory-progressive-reading
description: 对 Top-K 文献逐步深入阅读，获得与研究问题相关的关键原文片段和可追溯锚点。
---

# 文献渐进式深读

## 目标
对 Top-K 文献逐步深入阅读，获得与研究问题相关的关键原文片段和可追溯锚点。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

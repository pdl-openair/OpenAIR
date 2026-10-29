---
name: opentrajectory-evidence-extraction
description: 从深读结果中提取可回溯、可核验的证据节点。
---

# 证据抽取

## 目标
从深读结果中提取可回溯、可核验的证据节点。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

---
name: opentrajectory-report-generation
description: 将证据、机制链、已有结论、科研线索、假说和验证方案组装为可回溯科研结果包。
---

# 科研结果包组装

## 目标
将证据、机制链、已有结论、科研线索、假说和验证方案组装为可回溯科研结果包。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 中映射的 L3 原子 Skill 完成。
- 不得跨越当前节点提前生成下游产物。

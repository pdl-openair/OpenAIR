---
name: opentrajectory-evidence-schema-design
description: 根据科研问题动态确定需要抽取的实体、实验条件、性能指标、理论计算信息、关系类型和证据充分性规则。
---

# 证据模型设计

## 目标
根据科研问题动态生成任务级 EvidenceSchema，明确后续需要获取和核验的高价值证据目标。

## 编排边界
- 当前节点是 OpenTrajectory 的 L2 二级节点，负责定义“做什么”和阶段产物。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 映射的 `evidence-schema-design-skill` 完成。
- EvidenceSchema 是任务级证据充分性契约，不是固定领域字段模板。
- 不得跨越当前节点提前抽取文献证据、生成机制链、科研线索、假说或证据补全策略。

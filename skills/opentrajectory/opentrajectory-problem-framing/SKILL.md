---
name: opentrajectory-problem-framing
description: 一次完成研究对象、目标、输出意图、约束、未知、歧义与冲突的正式问题建模。
---

# 研究问题建模

## 目标

把用户当前科研问题冻结成一份可供后续研究直接使用的问题定义：明确研究什么、哪些条件需要遵守、哪些只是偏好、哪些信息未知或存在歧义/冲突，以及用户最终希望得到什么结果。

## 编排边界

- 当前节点是 OpenTrajectory 的正式问题建模工作，只形成问题定义，不设计检索策略。
- 研究对象、目标、输出意图、约束、未知、歧义与冲突属于同一次科研语义判断，不拆成两个串行 LLM 工作。
- `retrieval_strategy_design` 仍是独立下游工作，负责基于已经冻结的问题定义决定如何找证据。
- 具体执行由 `schemas/opentrajectory/substage-skill-map.yaml` 映射的 L3 原子 Skill 完成。

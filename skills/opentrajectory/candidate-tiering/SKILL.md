---
name: candidate-tiering
description: >-
  基于 candidate-source-scoring 的评分结果进行门控与分层，输出 source_tiers（core / mechanism_support /
  method_inspiration / benchmark_or_evaluation / background / excluded）以及 tiering_report。
  在 candidate-source-scoring 之后、topk-source-selection 之前运行。
---

# Candidate Tiering

## 概述

`candidate-tiering` 将 #9 输出的 `candidate_scores` 进行规则门控（gate）与分层（tier）。

## 能力边界

### 做什么
- 读取 `candidate_scores[]` 及评分解释字段
- 执行 off-domain 阻断（P08B-1E-c）、负向词阻断、metric-only 阻断、core 命中判断
- 输出每条候选的 tier/pool/reason 字段
- 生成 `tiering_report`

### 不做什么
- ❌ 不做 top-k 选择（#11）
- ❌ 不输出 `topk_selection` / `selected_for_p03` / `p03_acquisition_plan`
- ❌ 不调用 LLM、数据库或外部 API

## 输入
- `candidate_scores[]`（来自 #9；含 `score_breakdown.off_domain_penalty`，由 contract-aware scoring 写入）

## 门控优先级（P08B-1E-c）

按顺序评估，命中即停止：

1. **off-domain 阻断**：`score_breakdown.off_domain_penalty >= 0.6` → `tier=excluded`，`exclusion_reason=off_domain_blocked`
2. **负向词阻断**：`negative_penalty >= 0.6` 或 `negative_hits` 数量 ≥ 2 → `exclusion_reason=negative_context_blocked`
3. **metric-only 阻断**：`metric_only=true` 且 object/mechanism 均弱 → `exclusion_reason=metric_only_blocked`
4. 其余分层规则（core / mechanism_support / …）

单条 offtopic pattern 命中时 scoring 仅施加软惩罚（0.4），不会触发 tier 排除。

## 输出
- `source_tiers[]`
  - `candidate_id`, `dedupe_key`, `title`, `final_score`
  - `tier`, `pool`
  - `selection_reason`, `downrank_reason`, `exclusion_reason`
  - `core_ok`
  - `score_breakdown`, `matched_facets`, `negative_hits`, `provenance`
- `tiering_report`
  - `tier_counts`
  - `negative_blocked_count`
  - `metric_only_blocked_count`
  - `off_domain_blocked_count`（P08B-1E-c）
  - `degraded_input_count`
  - `risk_flags`（含 `off_domain_blocks_present` 当 off-domain 排除发生时）

## 迁移来源
- OpenTrajectory `p025_topk_selector.py`
  - `_gate_and_pool()` 及其门控阈值/辅助逻辑

## 状态
- status: draft
- fixture_status: example_only

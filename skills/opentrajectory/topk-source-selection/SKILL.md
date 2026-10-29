---
name: topk-source-selection
description: >-
  从 candidate-tiering 输出的 source_tiers 中进行动态 Top-K 选择，组装 selected_for_p03、
  supporting/method/excluded 以及 p03_acquisition_plan、risk_summary、selection_stats。
---

# TopK Source Selection

## 概述

`topk-source-selection` 负责在不重新评分、不重新分层的前提下，
从 #10 `source_tiers[]` 里做动态 Top-K 选择。

## 能力边界

### 做什么
- 基于 tier/pool/final_score 进行动态 Top-K 选择
- 支持 `core_min / dynamic_target / dynamic_cap / score_min / extended_score_min`
- 组装：
  - `selected_for_p03`
  - `extended_selected`
  - `supporting_papers`
  - `method_inspiration_papers`
  - `excluded_examples`
  - `selection_stats`
  - `p03_acquisition_plan`
  - `risk_summary`
- 输入缺失时提供降级输出（degraded）

### 不做什么
- ❌ 不做 scoring（#9）
- ❌ 不做 tiering（#10）
- ❌ 不输出 `candidate_scores` / `source_tiers` / `tiering_report`
- ❌ 不做持久化写入

## 输入
- `source_tiers[]`（来自 #10）
- 可选 `topk_config`

## 输出
- `topk_selection`
  - `selected_for_p03[]`
  - `extended_selected[]`
  - `supporting_papers[]`
  - `method_inspiration_papers[]`
  - `excluded_examples[]`
  - `selection_stats`
  - `p03_acquisition_plan`
  - `risk_summary`
  - `degraded`
  - `degradation_reasons[]`

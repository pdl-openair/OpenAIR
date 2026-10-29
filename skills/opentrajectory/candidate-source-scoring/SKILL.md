---
name: candidate-source-scoring
description: >-
  直接消费来源筛选结果，对已筛候选进行可解释的候选级多维评分，
  为 candidate-tiering 提供 candidate_scores；不执行筛选、分层或 Top-K。
---

# Candidate Source Scoring

## 职责

`candidate-source-scoring` 只负责一件事：

> 对 `source_screening_selection` 已筛候选进行候选级多维评分，并把评分依据与降级状态明确交给 `candidate-tiering`。

评分是排序/分层输入，不是科学真理，也不是最终来源选择。

## 正式输入

### `screened_candidates[]`

直接接收当前 `title-abstract-screening` 的候选对象，不把它们改造成另一套候选结构。

评分只处理 `screening_decision = include | maybe` 的候选；`exclude` 是上游已作出的筛选决定，scorer 只尊重该决定，不重新筛选。

每条候选使用：

- `candidate_id` / `dedupe_key` / `title`
- `abstract`（允许缺失）
- `screening_decision` / `screening_reason` / `relevance_signals`
- `access_status`
- `provenance`
- 其余来源元数据仅原样保留，不参与隐藏补全

若进入评分的候选缺少 `dedupe_key`，该候选不生成伪造身份；在 `scoring_report.unscored_candidates` 中明确报告。

### `research_context`

只承载 scorer 真正需要的科研评分上下文：

- `research_question`：当前研究问题
- `research_objects`：需要关注的研究对象
- `conditions`：重要条件/场景
- `results_or_metrics`：目标结果或指标
- `mechanisms`：与当前问题有关的机制词/机制描述
- `comparison_dimensions`：重要比较维度
- `critical_evidence_requirements`：关键证据要求
- `negative_signals`：负向上下文
- `off_domain_signals`：跨域/离题信号

除 `research_question` 外，其余维度可以显式给空数组表示“本轮不适用”；字段缺失表示上下文未知，必须进入 degraded 状态，不能自动补成已知。

## 评分语义

候选评分包含：

- 研究对象、机制、结果/指标、条件与比较维度匹配
- 上游筛选相关性状态
- 摘要与结构化相关信号能够提供的证据潜力
- `access_status` 表示的可访问性
- 关键证据要求覆盖
- negative / off-domain 软惩罚

`citation_count` 不参与 `final_score`，避免把高引用量替代为与当前研究问题的相关性。

`off_domain` 只产生可解释的软惩罚；是否排除由下游 `candidate-tiering` 按其正式规则执行。

## Degraded 语义

以下情况仍可评分，但必须显式标记：

- 缺摘要：`missing_abstract`
- `landing_page_only / metadata_only / blocked / unknown`：`access_degraded:*`
- 上游为 `maybe`：`screening_maybe`
- 关键证据要求未充分覆盖：`critical_evidence_requirements_incomplete`
- `research_context` 有维度缺失：`research_context_incomplete`

未知保持未知；degraded 不等于自动排除。

## 正式输出

输出 `candidate_scores[]` + `scoring_report`。

`candidate_scores[]` 保持当前 `candidate-tiering` 所需的直接交接字段，包括：

- `candidate_id` / `dedupe_key` / `title` / `provenance`
- `final_score`
- `score_breakdown`
- `matched_facets`
- `negative_hits`
- `must_have_hit_count`
- `metric_only`
- `facets_quality_flags`

其中 `matched_facets` 与 `facets_quality_flags` 是当前 scorer → tiering 公开输出字段；它们不代表 scorer 仍接收旧 `facets` 输入。

## 不做什么

- 不重新执行 title/abstract screening
- 不生成或修改 tier / pool
- 不执行 Top-K
- 不把 citation count 当作科学相关性
- 不从缺失字段推断确定事实
- 不调用 LLM、数据库或外部 API

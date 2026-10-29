# Experiment — Exploratory Execution

## 用户价值

用小规模真实执行判断继续、调整或停止，并保留负结果和阻断。

## Input
- `research_method_validation_design`
- `execution_request`

## Output
- `exploratory_execution`

## Acceptance
- `pilot_actual_config_environment_and_status_traceable`
- `raw_outputs_logs_resource_use_and_deviations_preserved`
- `blocked_failed_and_partial_are_valid_results`
- `negative_results_preserved`
- `go_adjust_stop_evidence_explicit`
- `pilot_not_promoted_to_final_conclusion`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 失败与不足

failed / partial / blocked 为合法结果；不得自动把探索结果升级为正式结论。

## 来源与重入边界

design→execution request→run→raw output 保持 lineage；设计/执行请求变化或明确再次 pilot 时新 instance。

## 目的

<!-- exploratory_execution: 在全量实验前进行小规模验证，确认方案可行性，避免资源浪费。 -->

## 试飞设置

<!-- pilot setup: 环境、数据子集、简化参数 -->

| Item | Value |
|------|-------|
| environment | <!-- GPU/CPU, framework version --> |
| data_subset | <!-- 使用的数据子集及比例 --> |
| parameters | <!-- 简化后的参数配置 --> |

## 基线测试

<!-- 1-2 baselines tested: 选择最强 1-2 个基线进行初步对比 -->

### Baseline 1: <!-- name -->

- **config:** <!-- 配置摘要 -->
- **result:** <!-- 初步结果 -->
- **notes:** <!-- 观察到的现象 -->

### Baseline 2: <!-- name -->

- **config:** <!-- -->
- **result:** <!-- -->
- **notes:** <!-- -->

## 错误分析

<!-- error analysis: 对试飞结果进行错误模式分析 -->

| Error Type | Frequency | Example | Hypothesis |
|-----------|-----------|---------|------------|
| <!-- --> | <!-- --> | <!-- --> | <!-- --> |

## 可行性评估

<!-- feasibility assessment: 综合判断方案是否可行 -->

| Criterion | Status | Notes |
|-----------|--------|-------|
| environment ready | yes/no | <!-- --> |
| data pipeline working | yes/no | <!-- --> |
| method runs without crash | yes/no | <!-- --> |
| preliminary results reasonable | yes/no | <!-- --> |
| resource usage acceptable | yes/no | <!-- --> |

## 决策

<!-- decision: proceed / adjust / redo -->

| Option | Description |
|--------|-------------|
| proceed | 结果符合预期，直接进入全量实验 |
| adjust | 需要局部调整参数或流程 |
| redo | 方案存在根本问题，需要重新设计 |

**Decision:** <!-- proceed / adjust / redo -->
**Rationale:** <!-- -->
**Date:** <!-- -->
**Next action:** <!-- -->

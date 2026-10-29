# OPE 结果验证与分析

## 用户价值

对真实 OPE 结果完成质量验证、分析和不确定性判断，保留失败与负结果。

## Input
- `experiment_execution`
- `experiment_design`
- `metric_definitions`

## Output
- `result_validation_analysis`

## Acceptance
- `raw_data_immutability_preserved`
- `transform_exclusion_and_cleaning_traceable`
- `analysis_follows_confirmed_plan_or_deviation_is_explicit`
- `effects_uncertainty_anomalies_and_confounders_explicit`
- `failed_and_partial_runs_included`
- `inconclusive_result_preserved_when_assumptions_fail`
- `fixed_cross_project_thresholds_not_invented`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 失败与不足

样本不足或分析假设不满足时输出 inconclusive / limited；不得删除坏结果制造显著性。

## 来源与重入边界

每个分析结果链接 raw run、transform/version 和 analysis plan；新增 run、清洗规则或分析计划变化时重入。

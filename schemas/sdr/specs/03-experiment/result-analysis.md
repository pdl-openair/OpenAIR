# SDR 结果分析

## 用户价值

把真实执行/结果记录转成可追溯的观察、效应、不确定性和分析证据。

## Input
- `accepted_execution_records`
- `analysis_plan`
- `metric_definitions`

## Output
- `result_analysis`

## Acceptance
- `observations_metrics_and_transforms_traceable`
- `analysis_follows_confirmed_plan_or_deviation_is_explicit`
- `effects_uncertainty_and_assumption_checks_explicit`
- `exclusions_and_missing_data_explicit`
- `nonquantitative_and_not_applicable_paths_supported`
- `negative_nonsignificant_and_insufficient_sample_results_preserved`
- `selective_exclusion_prohibited`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 失败与不足

inconclusive / insufficient sample / not-applicable 均为合法结果；禁止选择性剔除。

## 来源与重入边界

每个分析结果链接 raw run/source、transform 和 analysis plan；新增执行结果或 analysis plan revision 时重入。

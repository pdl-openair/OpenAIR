# Execution Validation -- Experiment Execution

<!-- Experiment run log with results -->

## 用户价值

真实执行已冻结 OPE 实验设计并保留完整运行事实、失败和偏差。

## Input
- `experiment_design`
- `execution_identity_bundle`

## Output
- `experiment_execution`

## Acceptance
- `actual_execution_only`
- `implementation_identity_explicit_when_created_or_modified`
- `config_seed_environment_and_command_traceable`
- `all_success_failure_partial_and_timeout_results_preserved`
- `raw_outputs_logs_and_resources_traceable`
- `design_deviation_explicit`
- `implementation_success_not_equated_with_experiment_success`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`implementation_requirement` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

blocked / failed / partial / timeout 均为正式结果；重试不得静默改变实验目标。

## 来源与重入边界

design→code revision→data→config→environment→run→raw output 保持全链 lineage；设计、实现、数据变化或明确重跑时新 instance。

## Execution Summary

<!-- Total experiments, total runs, environments tested -->

## Results per Experiment

| # | Experiment | Runs | Metric (Mean±Std) | Status |
|---|-----------|------|-------------------|--------|
| <!-- fill --> | | | | Complete/Failed |

## Key Observations

<!-- Notable findings during execution -->

## Raw Data Locations

<!-- Paths to raw result files -->

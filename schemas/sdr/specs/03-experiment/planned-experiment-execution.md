# SDR 计划实验执行

## 用户价值

仅执行已确认矩阵并完整保存真实运行、偏差、失败与未执行原因。

## Input
- `research_method_validation_design`
- `execution_matrix`
- `execution_identity_bundle`
- `execution_authorization`

## Output
- `planned_experiment_execution`

## Acceptance
- `only_approved_matrix_executed`
- `run_identity_config_environment_and_output_traceable`
- `actual_resource_use_and_deviation_explicit`
- `failed_partial_timeout_and_unexecuted_preserved`
- `negative_results_preserved`
- `research_goal_not_silently_changed`
- `existing_valid_execution_reused_when_applicable`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 失败与不足

blocked / failed / partial / timeout / unexecuted 均是正式状态；不得为追求完成率静默扩大或改变矩阵。

## 来源与重入边界

design/matrix→run identities→raw outputs 保持全链 lineage；显式新批次或 design revision 时新 instance。

# 结果可比性与达标判断

## 输入
- `reproduction_baseline_and_criteria`
- `reproduction_execution_record`
- `actual_metric_records`

## 输出
- `reproduction_comparison_judgment`

## 验收
- `comparability_checked_before_verdict`
- `actual_reference_difference_explicit`
- `predefined_rule_applied`
- `condition_mismatch_effect_explicit`
- `indeterminate_preserved_when_evidence_insufficient`

## 执行方法

```yaml
methodId: cs_reproduction_result_comparison
methodVersion: 1
semantics: role_graph_v1
roles:
- roleId: deterministic_comparison
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 对显式 actual、reference 与预先确定的比较规则进行确定性逐指标计算。
    requiredInputRefs: [actual_metrics, reference_metrics]
    outputArtifactTypes: [deterministic_metric_comparison]
    judgmentRequirement: deterministic
- roleId: comparison_judgment_closure
  requirement: required
  dependsOn: [deterministic_comparison]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 先核验条件可比性，再依据冻结规则与确定性计算形成科研达标判断。
    requiredInputRefs: [reproduction_baseline_and_criteria, reproduction_execution_record, actual_metric_records, deterministic_comparison]
    outputArtifactTypes: [reproduction_comparison_judgment]
    judgmentRequirement: domain_professional
completionRoleIds: [comparison_judgment_closure]
stopConditions: []
qualityConstraints:
- comparability_precedes_verdict
- predefined_rules_only
- deterministic_differences_are_not_recalculated_by_model
- insufficient_evidence_remains_indeterminate
```

## 研究工作说明
先核验任务范围、数据划分、预处理、训练与评测方式、指标口径、实现差异及相关随机性或硬件条件，再按有依据的判据判断各项目标是否达标。条件不足时保留差异并给出无法判定，不迁就结果修改判据。

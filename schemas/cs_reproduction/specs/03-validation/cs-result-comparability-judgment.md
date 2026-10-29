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

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
deterministicValidatorRefs: [reference_metric_comparison, reproduction_comparison_closure]
```

## 执行方法

```yaml
methodId: cs_reproduction_result_comparison
methodVersion: 2
semantics: role_graph_v1
roles:
- roleId: comparability_assessment
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 基于真实执行记录、正式实测指标记录与复现基线，判断论文参考结果和本次结果是否具有比较资格；不得形成达标结论。
    requiredInputRefs: [reproduction_baseline_and_criteria, reproduction_execution_record, actual_metric_records]
    outputArtifactTypes: [reproduction_comparability_assessment]
    judgmentRequirement: domain_professional
- roleId: deterministic_comparison
  requirement: required
  dependsOn: [comparability_assessment]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 仅在可比性已由专业判断完整建立时，对正式实测指标、参考指标与预先确定规则做确定性逐指标计算。
    requiredInputRefs: [actual_metric_records, reference_metrics, reproduction_comparability_assessment]
    outputArtifactTypes: [deterministic_metric_comparison]
    judgmentRequirement: deterministic
- roleId: comparison_judgment_closure
  requirement: required
  dependsOn: [comparability_assessment, deterministic_comparison]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 将可比性专业判断与确定性比较闭合为最终三态结论；不可比、判据缺失或证据不足必须为 indeterminate。
    requiredInputRefs: [reproduction_comparability_assessment, deterministic_comparison]
    outputArtifactTypes: [reproduction_comparison_judgment]
    judgmentRequirement: deterministic
completionRoleIds: [comparison_judgment_closure]
stopConditions: []
qualityConstraints:
- comparability_precedes_verdict
- predefined_rules_only
- deterministic_differences_are_not_recalculated_by_model
- final_verdict_is_deterministic
- insufficient_evidence_remains_indeterminate
```

## 研究工作说明
先核验任务范围、实现、数据划分、预处理、评测方式、指标口径、随机性和硬件环境是否支持直接比较。只有可比性证据完整时，确定性代码才应用冻结判据计算达标或未达标；不可比、判据缺失、实测记录不完整或证据不足时输出 `indeterminate`。

# 真实执行与结果取得

## 输入
- `reproduction_experiment_design`
- `execution_readiness_record`

## 输出
- `reproduction_execution_record`
- `actual_metric_records`

## 验收
- `actual_execution_traceable`
- `frozen_inputs_and_actual_usage_consistent`
- `raw_outputs_traceable`
- `actual_results_not_filled_from_reference`
- `partial_failure_and_unknowns_preserved`

## 执行方法

```yaml
methodId: cs_reproduction_real_execution
methodVersion: 1
semantics: role_graph_v1
roles:
- roleId: experiment_execution
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 使用已验证环境按冻结参数真实运行实验并固定进程与原始输出事实。
    requiredInputRefs: [executable, argv, working_directory, timeout_seconds]
    outputArtifactTypes: [raw_reproduction_execution]
    judgmentRequirement: deterministic
- roleId: metric_extraction
  requirement: required
  dependsOn: [experiment_execution]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 只从本次真实运行关联的明确结果来源确定性提取实测指标。
    requiredInputRefs: [execution_record_relative_path, result_source, metrics]
    outputArtifactTypes: [raw_actual_metric_records]
    judgmentRequirement: deterministic
- roleId: execution_record_closure
  requirement: required
  dependsOn: [experiment_execution, metric_extraction]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 对真实运行、冻结输入、实际使用与原始输出证据进行专业闭合。
    requiredInputRefs: [reproduction_experiment_design, execution_readiness_record, raw_execution_record, raw_actual_metrics]
    outputArtifactTypes: [reproduction_execution_record]
    judgmentRequirement: domain_professional
- roleId: actual_metric_records_closure
  requirement: required
  dependsOn: [experiment_execution, metric_extraction]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 将确定性提取结果闭合为与本次执行关联的正式实测指标记录。
    requiredInputRefs: [reproduction_experiment_design, execution_readiness_record, raw_execution_record, raw_actual_metrics]
    outputArtifactTypes: [actual_metric_records]
    judgmentRequirement: domain_professional
completionRoleIds: [execution_record_closure, actual_metric_records_closure]
stopConditions: []
qualityConstraints:
- actual_execution_only
- reference_values_never_fill_actual_results
- frozen_inputs_and_actual_usage_are_compared
- partial_failure_and_unknowns_are_preserved
```

## 研究工作说明
按已确认方案和有效条件完成授权范围内的真实实验，保存代码、资源、环境、实际配置、运行状态、原始输出与实际结果之间的对应关系。参考值、历史成绩和计划字段不得补入本次实测结果。

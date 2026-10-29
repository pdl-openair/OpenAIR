# 复现实验方案设计

## 输入
- `reproduction_baseline_and_criteria`

## 输出
- `reproduction_experiment_design`

## 验收
- `target_to_experiment_mapping_complete`
- `implementation_and_protocol_explicit`
- `result_acquisition_method_explicit`
- `resource_and_cost_boundary_explicit`
- `source_required_and_system_selected_details_distinguished`
- `runtime_pending_details_explicit`
- `project_constraints_and_intentional_deviations_explicit`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 执行方法

```yaml
methodId: cs_reproduction_experiment_design_grounding
methodVersion: 1
semantics: role_graph_v1
roles:
- roleId: execution_source_interpretation
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 从基线阶段已经固定的官方来源中解释资源、环境、执行与结果读取语义，来源未说明的事实保持未知。
    requiredInputRefs: [reproduction_baseline_and_criteria, source_package, reference_metrics]
    outputArtifactTypes: [reproduction_execution_plan_interpretation]
    judgmentRequirement: domain_professional
- roleId: execution_fact_assembly
  requirement: required
  dependsOn: [execution_source_interpretation]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 确定性核验官方来源引用并组装具有稳定身份、来源记录和明确未知的执行计划事实。
    requiredInputRefs: [source_package, interpretation, reference_metrics]
    outputArtifactTypes: [reproduction_execution_plan_facts]
    judgmentRequirement: deterministic
- roleId: experiment_design_closure
  requirement: required
  dependsOn: [execution_fact_assembly]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 结合正式基线、官方执行事实和当前项目边界，形成可执行且不过度推断的复现实验方案。
    requiredInputRefs: [reproduction_baseline_and_criteria, execution_plan_facts]
    outputArtifactTypes: [reproduction_experiment_design]
    judgmentRequirement: domain_professional
completionRoleIds: [experiment_design_closure]
stopConditions: []
qualityConstraints:
- official_execution_facts_preserve_lineage
- project_constraints_are_explicit
- source_required_system_selected_and_deviation_are_separate
- missing_required_fact_blocks_execution
- plans_are_not_execution_facts
```

## 研究工作说明
形成足以检验当前复现目标的实验安排，明确采用实现、资源、关键配置、训练或评测协议、结果取得方式以及必要的重复与汇总规则。官方来源要求、系统选择、项目约束和有意偏离分别记录；必要来源或授权边界不足时明确阻断。方案只定义研究与执行要求，不把计划记为已执行。

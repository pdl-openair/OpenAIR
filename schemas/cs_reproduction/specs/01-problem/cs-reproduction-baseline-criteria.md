# 复现基线与判据确定

## 输入
- `research_goal`

## 输出
- `reproduction_baseline_and_criteria`

## 验收
- `reproduction_target_explicit`
- `reference_source_traceable`
- `target_condition_reference_mapped`
- `comparison_rule_source_grounded`
- `unknowns_and_conflicts_preserved`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 执行方法

```yaml
methodId: cs_reproduction_baseline_grounding
methodVersion: 1
semantics: role_graph_v1
roles:
- roleId: input_structuring
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 从冻结研究目标中只提取明确给出的官方代码仓库与 revision，缺失项保持未知。
    requiredInputRefs: [research_goal]
    outputArtifactTypes: [cs_reproduction_execution_inputs]
    judgmentRequirement: none
- roleId: code_identity_acquisition
  requirement: required
  dependsOn: [input_structuring]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 真实获取官方代码仓库并固定、核验明确给出的 revision。
    requiredInputRefs: [repository, revision]
    outputArtifactTypes: [repository_revision_acquisition_result]
    judgmentRequirement: deterministic
- roleId: official_source_acquisition
  requirement: required
  dependsOn: [code_identity_acquisition]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 从已固定代码版本读取受控官方文本并保留读取证据。
    requiredInputRefs: [code_source]
    outputArtifactTypes: [official_reference_source_package]
    judgmentRequirement: deterministic
- roleId: reference_interpretation
  requirement: required
  dependsOn: [official_source_acquisition]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 仅依据受控官方来源解释与目标相关的参考指标、实验条件和比较规则依据。
    requiredInputRefs: [research_goal, source_package]
    outputArtifactTypes: [reference_metric_interpretation]
    judgmentRequirement: domain_professional
- roleId: reference_fact_assembly
  requirement: required
  dependsOn: [official_source_acquisition, reference_interpretation]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 确定性核验来源引用、类型、规则和冲突并组装正式 reference facts。
    requiredInputRefs: [source_package, interpretation]
    outputArtifactTypes: [reference_metric_facts]
    judgmentRequirement: deterministic
- roleId: baseline_closure
  requirement: required
  dependsOn: [input_structuring, reference_fact_assembly]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 基于研究目标和可用的正式 reference facts 形成可追溯基线；来源不足时保留未知并判为 incomplete。
    requiredInputRefs: [research_goal, reference_facts]
    outputArtifactTypes: [reproduction_baseline_and_criteria]
    judgmentRequirement: domain_professional
completionRoleIds: [baseline_closure]
stopConditions: []
qualityConstraints:
- formal_reference_facts_only
- actual_metrics_are_forbidden
- missing_source_degrades_to_incomplete
- comparison_rules_must_be_source_grounded
```

## 研究工作说明
确定本次具体检验的论文结果、对应实验条件、参考结果、来源与比较判据。已有正式结果只能在来源、版本、条件、当前目标和新增证据核验后复用；来源没有规定的容差保持未知，不根据实际结果反向修改判据。

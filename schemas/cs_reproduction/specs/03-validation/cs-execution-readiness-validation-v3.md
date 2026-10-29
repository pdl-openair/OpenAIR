# 执行准备与条件核验

## 输入
- `reproduction_experiment_design`
- `reproduction_baseline_and_criteria`

## 输出
- `execution_readiness_record`

## 验收
- `code_resource_environment_traceable`
- `execution_entry_verified`
- `required_inputs_delivered_or_gap_explicit`
- `readiness_conclusion_scope_explicit`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 执行方法

```yaml
methodId: cs_reproduction_execution_readiness
methodVersion: 1
semantics: role_graph_v1
roles:
- roleId: code_identity_acquisition
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 真实获取并固定实验方案声明的代码仓库与 revision。
    requiredInputRefs: [repository, revision]
    outputArtifactTypes: [repository_revision_acquisition_result]
    judgmentRequirement: deterministic
- roleId: resource_identity_acquisition
  requirement: optional
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 真实获取并固定实验方案声明的模型或数据资源身份。
    requiredInputRefs: [resources]
    outputArtifactTypes: [resource_identity_acquisition_result]
    judgmentRequirement: deterministic
- roleId: environment_setup
  requirement: optional
  dependsOn: [code_identity_acquisition]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: continue_degraded
  parallelism: single
  qualificationContract:
    objective: 按实验方案真实建立隔离环境并验证关键依赖和执行入口。
    requiredInputRefs: [python_version, requirements, verify_imports]
    outputArtifactTypes: [environment_setup_record]
    judgmentRequirement: deterministic
- roleId: readiness_closure
  requirement: required
  dependsOn: [code_identity_acquisition, resource_identity_acquisition, environment_setup]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 核验代码、资源、环境和入口证据，形成有明确范围与缺口的执行准备结论。
    requiredInputRefs: [reproduction_experiment_design, reproduction_baseline_and_criteria, code_identity, resource_identities, environment_record]
    outputArtifactTypes: [execution_readiness_record]
    judgmentRequirement: domain_professional
completionRoleIds: [readiness_closure]
stopConditions: []
qualityConstraints:
- real_code_resource_environment_evidence_only
- readiness_does_not_imply_execution
- missing_inputs_remain_explicit
```

## 研究工作说明
面向真实执行入口准备并核验代码、必要资源、环境、配置和输入条件，明确哪些实验当前可执行以及缺失条件的影响。准备完成只证明执行条件，不替代真实运行和实际资源使用证据。

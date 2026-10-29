# 研究记录建立

## 输入
- `raw_research_materials`
- `accepted_existing_research_records`
- `current_judgment_target`

## 输出
- `research_records`

## 验收
- `source_traceability_preserved`
- `object_condition_result_source_aligned`
- `observation_interpretation_identity_separated`
- `unknown_fields_not_fabricated`
- `existing_records_reused_only_when_currently_applicable`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
把当前问题需要使用的论文、实验、计算、数据库或代码运行结果整理为可追溯 ResearchRecord，保持研究对象、条件、结果和来源的对应关系。不同实验、样品或运行不默认合并；原始观测、作者解释和用户提供信息保留各自身份与核验程度。已有记录经当前项目接受且适用时直接复用；缺少材料或关键字段时保留缺口，不补造结果。本职责只确认材料报告了什么，不判断它对当前结论的证据用途。

## 执行方法

```yaml
methodId: opentrajectory_research_record_establishment_v1
methodVersion: 1
semantics: role_graph_v1
roles:
  - roleId: research_context_modeling
    requirement: required
    dependsOn: []
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 将当前科研请求整理为检索与证据建模可直接使用的研究上下文。
      requiredInputRefs: []
      outputArtifactTypes: [opentrajectory.problem_framing]
      judgmentRequirement: none
  - roleId: retrieval_strategy
    requirement: required
    dependsOn: [research_context_modeling]
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 根据研究上下文形成覆盖当前问题的检索矩阵。
      requiredInputRefs: []
      outputArtifactTypes: [opentrajectory.retrieval_strategy_design]
      judgmentRequirement: none
  - roleId: evidence_schema
    requirement: required
    dependsOn: [research_context_modeling, retrieval_strategy]
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 明确本次研究记录需要关注的对象、条件、结果、来源与缺口。
      requiredInputRefs: []
      outputArtifactTypes: [opentrajectory.evidence_schema_design]
      judgmentRequirement: none
  - roleId: source_acquisition
    requirement: required
    dependsOn: [retrieval_strategy]
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 按已确认的检索矩阵获取并保留可追溯的候选研究来源。
      requiredInputRefs: []
      outputArtifactTypes: [opentrajectory.source_acquisition]
      judgmentRequirement: none
  - roleId: source_screening
    requirement: required
    dependsOn: [evidence_schema, research_context_modeling, source_acquisition]
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 按当前研究问题和证据需求判断候选来源的科研相关性与阅读优先级。
      requiredInputRefs: []
      outputArtifactTypes: [opentrajectory.source_screening_selection]
      judgmentRequirement: domain_professional
  - roleId: progressive_reading
    requirement: required
    dependsOn: [evidence_schema, source_screening]
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 对入选来源进行有界深读并保留可核验的原文片段与来源锚点。
      requiredInputRefs: []
      outputArtifactTypes: [opentrajectory.progressive_reading]
      judgmentRequirement: none
  - roleId: research_record_extraction
    requirement: required
    dependsOn: [evidence_schema, progressive_reading]
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 从已确认材料中形成对象、条件、结果与来源严格对应的可追溯研究记录。
      requiredInputRefs: []
      outputArtifactTypes: [research_records]
      judgmentRequirement: domain_professional
completionRoleIds: [research_record_extraction]
stopConditions: []
qualityConstraints:
  - 原始观测、作者解释和模型解释必须保持身份分离
  - 只允许使用当前方法内已取得且可追溯的材料建立研究记录
  - 研究记录建立不提前承担证据判断或最终结论职责
  - 缺失对象、条件、结果或来源时不得补造研究记录
```

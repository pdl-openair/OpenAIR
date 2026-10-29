# 证据评估与研究综合

## 输入
- `current_judgment_target`
- `research_records`
- `accepted_existing_evidence`

## 输出
- `evidence_assessment_and_research_synthesis`

## 验收
- `evidence_relationships_traceable_to_records_and_sources`
- `support_refutation_limitation_distinguished`
- `source_quality_independence_and_applicability_explicit`
- `comparability_and_condition_differences_explicit_when_relevant`
- `conflicts_and_evidence_gaps_preserved`
- `absence_of_found_evidence_not_overgeneralized`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
围绕当前判断目标建立 Evidence 的支持、反驳或限制关系，说明来源质量、独立性、适用条件、冲突和证据缺口，并综合形成主要发现。跨研究比较时明确比较对象、指标口径和条件差异，区分可直接比较、有限参考和不可直接比较；不涉及跨研究比较时只说明适用性。缺少证据、未找到证据和反向证据分别表达，相关性、因果性和推断分别表达。

## 执行方法

```yaml
methodId: opentrajectory_evidence_assessment_synthesis_v1
methodVersion: 1
semantics: role_graph_v1
roles:
  - roleId: evidence_assessment
    requirement: required
    dependsOn: []
    capabilityKinds: [skill]
    releasePolicy:
      kind: always
    failurePolicy: fail_closed
    parallelism: single
    qualificationContract:
      objective: 基于当前可用的 ResearchRecords 判断其对当前问题的支持、反驳、限制、冲突与证据缺口，并保留记录可追溯性。
      requiredInputRefs: []
      outputArtifactTypes: [evidence_assessment_and_research_synthesis]
      judgmentRequirement: domain_professional
completionRoleIds: [evidence_assessment]
stopConditions: []
qualityConstraints:
  - 每一项证据判断只能引用本次输入中存在的 ResearchRecord
  - 不得把缺失字段、作者解释或不可比条件提升为已确立的结论
  - 冲突、局限和证据缺口必须对用户可见
```

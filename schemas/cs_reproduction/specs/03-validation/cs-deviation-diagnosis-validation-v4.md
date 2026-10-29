# 偏差诊断与验证

## 输入
- `reproduction_baseline_and_criteria`
- `reproduction_comparison_judgment`
- `reproduction_execution_record`
- `reproduction_experiment_design`

## 输出
- `reproduction_deviation_diagnosis`

## 验收
- `formal_entry_condition_and_decision_value_explicit`
- `evidence_supported_causes_separated_from_hypotheses`
- `operational_failures_excluded_from_scientific_causes`
- `valuable_validation_routes_to_confirmed_definition`
- `validation_outcomes_mapped_to_judgment_changes`
- `remaining_unknowns_preserved`
- `limited_closure_allowed_when_validation_has_no_value`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 执行方法

```yaml
methodId: cs_reproduction_deviation_diagnosis
methodVersion: 1
semantics: role_graph_v1
roles:
- roleId: diagnosis_and_validation
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 基于正式比较结论、真实执行记录、实验方案与基线，区分证据支持原因、待验证假设和未知；不得改写 B05 比较 verdict。
    requiredInputRefs: [reproduction_baseline_and_criteria, reproduction_comparison_judgment, reproduction_execution_record, reproduction_experiment_design]
    outputArtifactTypes: [reproduction_deviation_diagnosis]
    judgmentRequirement: domain_professional
completionRoleIds: [diagnosis_and_validation]
stopConditions: []
qualityConstraints:
- b05_verdict_remains_comparison_authority
- hypotheses_are_not_promoted_to_facts
- operational_failures_are_not_scientific_causes
- unknowns_remain_explicit
```

## 研究工作说明
仅在未达标、异常高分、运行间差异、来源冲突或执行偏离可能实质改变结论或下一步时进入。先明确进入条件与决策价值，再区分已有证据支持原因、待验证解释和剩余未知；一般网络、文件、进程等运行故障由执行能力处理，不得包装为科研根因。

必要验证必须指向当前已确认 ResearchDesign 中受影响的正式 CS Definition，并说明至少两种可区分结果分别会怎样改变哪个判断；实际验证由后续 ExecutionDecision / WorkUnit 执行，本职责不把验证建议记为已执行。验证结果形成后重新核验受影响的条件、执行、比较或诊断；需要新增研究任务或改变已确认边界时明确标记需方案变更，不自动扩展研究范围。缺乏进一步验证价值时允许保留未知并带限制收口，不以查清全部原因阻塞未达标或无法判定结论。

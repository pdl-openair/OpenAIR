# 复现结论与下一步建议

## 输入
- `reproduction_baseline_and_criteria`
- `reproduction_experiment_design`
- `reproduction_execution_record`
- `actual_metric_records`
- `reproduction_comparison_judgment`

## 可选输入
已接受的 `reproduction_deviation_diagnosis` 可补充已证实原因、剩余未知和有价值的验证路径；它不得改写正式三态比较结论。

## 输出
- `reproduction_conclusion`

## 验收
- `reproduction_question_answered_directly`
- `conclusion_scope_matches_evidence`
- `comparison_verdict_preserved_deterministically`
- `execution_status_preserved_deterministically`
- `execution_and_comparison_evidence_traceable`
- `evidence_sufficiency_consistent_with_formal_evidence`
- `limitations_and_unknowns_explicit`
- `diagnosis_hypotheses_not_promoted_to_fact`
- `next_step_changes_judgment_or_closes_scope`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 执行方法

```yaml
methodId: cs_reproduction_conclusion_closure
methodVersion: 1
semantics: role_graph_v1
roles:
- roleId: conclusion_synthesis
  requirement: required
  dependsOn: []
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 基于正式基线、实验边界、真实执行、实测结果、三态比较和可选有效诊断，形成范围、解释、限制与下一步的专业综合草稿。
    requiredInputRefs: [reproduction_baseline_and_criteria, reproduction_experiment_design, reproduction_execution_record, actual_metric_records, reproduction_comparison_judgment]
    outputArtifactTypes: [reproduction_conclusion_draft]
    judgmentRequirement: domain_professional
- roleId: conclusion_closure
  requirement: required
  dependsOn: [conclusion_synthesis]
  capabilityKinds: [skill]
  releasePolicy: {kind: always}
  failurePolicy: fail_closed
  parallelism: single
  qualificationContract:
    objective: 确定性保留比较三态、执行状态与正式证据引用，合并已知未知并闭合最终复现结论结构。
    requiredInputRefs: [reproduction_conclusion_draft, reproduction_baseline_and_criteria, reproduction_experiment_design, reproduction_execution_record, actual_metric_records, reproduction_comparison_judgment]
    outputArtifactTypes: [reproduction_conclusion]
    judgmentRequirement: deterministic
completionRoleIds: [conclusion_closure]
stopConditions: []
qualityConstraints:
- comparison_verdict_is_single_authority
- execution_status_is_projected_from_formal_execution_record
- determinate_scientific_verdict_requires_sufficient_evidence
- conclusion_evidence_refs_must_exist_in_formal_inputs
- diagnosis_hypotheses_remain_unconfirmed
- next_steps_target_confirmed_cs_definitions_only
- final_summary_does_not_create_new_scientific_facts
```

## 研究工作说明
直接回答当前复现范围到什么程度，并分别表达程序执行状态、证据充分性和科研达标判断。专业综合只负责范围、解释、限制、未知和下一步；最终三态必须由正式 `reproduction_comparison_judgment` 确定性映射，执行状态必须来自正式 `reproduction_execution_record`，不得在汇总层重新判断或改写。

结论使用正式基线、实验边界、真实执行、实测结果和比较证据形成可追溯记录。可选诊断只允许消费 `evidence_supported_causes`、剩余未知和尚未执行的验证路径；`hypotheses` 不得升级为事实，`validation_actions` 不得描述为已经完成。下一步只指向当前 CS Workflow 中能够改变判断的正式 Definition；需要改变 ResearchDesign 时明确标记，不在结论中自动扩展研究范围。部分结果只支持对应范围，`indeterminate` 不得改写为成功或失败。

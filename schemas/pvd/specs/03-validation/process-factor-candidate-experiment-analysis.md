# PVD 工艺因素与候选实验方向分析

## 用户价值

判断下一轮最值得改变什么、保持什么和验证什么，并形成可以进入可行性核验的具体候选实验组。

## 项目上下文

`PVDProjectContext` 是本轮冻结且可追溯的项目事实包，包含 structured context、recommendation parameter set、run preferences 以及 revision/lineage；本职责消费该事实包，不重复解析用户上下文，也不形成第六个 Workflow 节点。

## Input
- `PVDProjectContext`

## Output
- `candidate_analysis`

## Acceptance
- `candidate_parameter_identity_and_units_follow_project_context`
- `multiple_reasonable_directions_may_coexist`
- `exploration_ranges_do_not_replace_concrete_candidate_settings`
- `user_supplied_candidates_may_be_assessed_without_forced_replacement`
- `candidate_settings_not_recorded_as_executed_facts`
- `candidate_gap_explicit_when_no_verifiable_candidate_can_be_formed`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

已有且当前适用的 `evidence_applicability`、`feedback_analysis` 和用户已有候选均为可选输入；缺失时保持缺口，不通过重新解析用户上下文补值。

# PVD 本轮实验方案与参数推荐

## 用户价值

在当前证据、实验条件和资源允许范围内，从已经核验的候选中形成科研用户可以直接执行的下一轮实验决策。

## 项目上下文

`PVDProjectContext` 是本轮冻结且可追溯的项目事实包，包含 structured context、recommendation parameter set、run preferences 以及 revision/lineage；本职责消费该事实包，不重复解析用户上下文，也不形成第六个 Workflow 节点。

## Input
- `PVDProjectContext`
- `candidate_analysis`
- `feasibility_validation`

## Output
- `round_experiment_plan_parameter_recommendation`

## Acceptance
- `only_feasibility_validated_candidates_selected`
- `no_new_parameter_settings_introduced_during_recommendation`
- `changed_execution_conditions_require_revalidation`
- `executable_and_conditional_plans_distinguished`
- `no_usable_candidate_preserved_without_fabricated_groups`
- `parameter_scope_follows_current_project_context`
- `candidate_and_feasibility_lineage_preserved`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

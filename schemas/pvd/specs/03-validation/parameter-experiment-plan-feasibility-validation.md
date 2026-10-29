# PVD 参数与实验方案可行性核验

## 用户价值

判断具体候选在当前真实实验条件下能否执行，以及采用时必须满足哪些条件。

## 项目上下文

`PVDProjectContext` 是本轮冻结且可追溯的项目事实包，包含 structured context、recommendation parameter set、run preferences 以及 revision/lineage；本职责消费该事实包，不重复解析用户上下文，也不形成第六个 Workflow 节点。

## Input
- `PVDProjectContext`
- `candidate_analysis`

## Output
- `feasibility_validation`

## Acceptance
- `conditional_release_requires_explicit_actionable_preconditions`
- `unknown_critical_safety_or_equipment_facts_do_not_become_feasible`
- `hard_constraint_conflicts_block_adoption`
- `failed_or_unknown_validation_not_treated_as_feasible`
- `candidate_parameter_values_are_not_silently_modified`
- `scientific_feasibility_does_not_replace_human_safety_authority`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

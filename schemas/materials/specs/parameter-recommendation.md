# Spec — Flux Parameter Recommendation（Flux 制备参数推荐）

<!-- Substage: parameter_recommendation
     Stage: solution_design
     L2 skill: parameter-recommendation
     L3 skills: materials-parameter-recommend-flux -->

## 用户价值

为目标材料形成可追溯、可执行但尚未执行的制备参数建议。

## Input
- `target_material_candidate`
- `material_stability_information`

## Output
- `parameter_recommendation`

## Acceptance
- `parameter_basis_traceable`
- `temperature_profile_and_process_units_explicit`
- `expected_phase_and_alternative_paths_explicit`
- `laboratory_constraints_respected_when_known`
- `risks_unknowns_and_human_confirmation_boundaries_explicit`
- `recommendation_not_recorded_as_executed_fact`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`laboratory_constraints` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

依据不足时输出 provisional / needs_input；不得编造相图、溶解度或设备事实。

## 来源与重入边界

每个推荐参数必须链接候选材料、稳定性/氧化态依据或明确的系统设计选择；候选、稳定性信息或设备约束变化时重入。

## 目标

为上游给定的新晶体候选，推荐**Flux 生长法制备参数方案**（flux 成分、温度曲线、冷却速率、坩埚、气氛等），作为后续合成的解法设计。

## 与上下游的衔接

- **上游**：`candidate_generation` / `database_search`
- **下游**：`synthesis_execution`（按方案执行合成）

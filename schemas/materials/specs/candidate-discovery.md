# Materials 候选发现

## 用户价值

在约束与目标下形成可追溯的已有/新生成候选集合，并诚实保留无可行候选和部分失败。

## Input
- `materials_research_goal`
- `candidate_constraints`
- `candidate_objectives`

## Output
- `candidate_discovery`

## Acceptance
- `constraints_and_objectives_preserved`
- `retrieved_and_generated_candidates_distinguished`
- `candidate_identity_structure_and_properties_traceable`
- `property_source_or_prediction_type_explicit`
- `coverage_and_partial_failures_explicit`
- `zero_feasible_candidates_preserved`
- `duplicate_handling_and_run_lineage_traceable`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`seed_candidates` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

数据库无命中、生成 partial failure、0 feasible 均为合法结果；不得伪造候选。

## 来源与重入边界

区分数据库来源与生成 run lineage；所有候选属性标注来源、预测或计算类型；约束、目标或 shortage 改变时重入。

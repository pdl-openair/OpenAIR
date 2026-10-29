# 下一步研究建议

## 输入
- `current_research_conclusion`
- `key_unknowns_and_candidate_explanations`
- `confirmed_resource_time_cost_risk_boundaries`

## 输出
- `next_research_recommendation`

## 验收
- `recommendation_linked_to_current_conclusion_and_unknowns`
- `required_new_evidence_and_observation_target_explicit`
- `how_possible_results_change_current_judgment_explicit`
- `priority_or_parallel_choice_explained_when_multiple_paths_exist`
- `no_research_expansion_when_expected_value_is_low`
- `recommendation_separated_from_execution_and_completion_authority`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
给出优先推进的研究方向及理由，说明需要获得什么证据、重点观察什么结果，以及不同结果将怎样改变当前判断。存在多个合理方向时给出可解释的优先顺序或并列选择，不制造唯一最优；新增信息预期难以改变判断且成本较高时，允许建议当前范围无需继续研究。本职责只给科研建议，实际执行仍由 ExecutionDecision 决定，项目是否结束仍由 CompletionDecision 判断。

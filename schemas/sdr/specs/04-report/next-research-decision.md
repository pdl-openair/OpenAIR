# SDR 下一步研究决策

## 用户价值

依据当前结论与关键未知决定继续、修正、分支、暂缓或停止，不把建议变成执行授权。

## Input
- `research_conclusion`

## Output
- `next_research_decision`

## Acceptance
- `decision_links_to_conclusion_unknowns_failures_or_evidence`
- `continue_refine_branch_hold_and_stop_are_all_valid`
- `hypothesis_revision_preserves_version_lineage`
- `research_leads_prioritized_by_discriminating_value`
- `possibilities_not_promoted_to_findings`
- `resource_time_and_cost_boundaries_respected`
- `recommendation_does_not_grant_execution_authority`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`unresolved_questions`、`negative_results`、`resource_boundaries` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

资源或证据不足时可 hold / stop；建议不会自动转成执行授权。

## 来源与重入边界

decision 链接 conclusion、findings/negative results 和 resource constraints；结论、未知、资源或新证据变化时重入。

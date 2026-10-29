# OPE 科研结论与建议

## 用户价值

直接回答 OPE 研究问题，并基于已验证结果给出有边界的结论与下一步建议。

## Input
- `question_formulation`
- `result_validation_analysis`

## Output
- `research_conclusion_recommendation`

## Acceptance
- `research_question_answered_directly`
- `supported_not_supported_and_inconclusive_distinguished`
- `conclusion_strength_not_exceed_evidence`
- `negative_and_failed_results_contribute_to_conclusion`
- `limitations_and_uncertainty_explicit`
- `next_recommendation_targets_unresolved_judgment`
- `execution_completion_not_equated_with_scientific_success`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`evidence_review` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

证据不足时输出 inconclusive / needs_more_evidence；不得生成无依据的正向结论。

## 来源与重入边界

每条结论链接 question、result analysis 和相关 evidence；accepted result/evidence 或研究问题实质变化时重入。

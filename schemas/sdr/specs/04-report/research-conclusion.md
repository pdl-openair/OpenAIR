# SDR 科研结论

## 用户价值

在 findings 与可靠性审计之后直接回答研究问题，明确范围、不确定性和未决问题。

## Input
- `research_problem_framing`
- `findings_synthesis`
- `result_reliability_audit`

## Output
- `research_conclusion`

## Acceptance
- `research_question_answered_directly`
- `conclusion_strength_not_exceed_findings_evidence_and_audit`
- `fact_inference_and_unknown_distinguished`
- `scope_boundaries_counterevidence_and_uncertainty_explicit`
- `negative_and_inconclusive_answers_allowed`
- `unresolved_questions_preserved`
- `report_generation_not_used_as_conclusion_authority`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`evidence_limitations` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

缺少足够 accepted findings / audit 时返回 inconclusive / needs_evidence；不得编造结论。

## 来源与重入边界

每条结论链接 problem question、findings 和 reliability audit；这些 Authority 变化时重入。

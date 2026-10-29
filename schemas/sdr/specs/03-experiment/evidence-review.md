# SDR 证据评审

## 用户价值

形成来源可追溯、筛选理由清楚、覆盖边界明确的证据评审结果。

## Input
- `research_problem_framing`

## Output
- `evidence_review`

## Acceptance
- `search_boundary_and_source_coverage_explicit`
- `source_locators_and_evidence_rows_traceable`
- `candidate_gap_and_not_a_gap_distinguished`
- `gap_confidence_bounded_by_coverage`
- `zero_hit_unavailable_and_partial_coverage_preserved`
- `field_wide_gap_not_claimed_from_limited_evidence`
- `evidence_needed_explicit`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`evidence_boundary`、`supplied_sources` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

检索不可用、无命中或 coverage partial 均需保留；coverage 不足时不得宣称领域级 gap。

## 来源与重入边界

query→source→evidence→gap 保持全链 lineage；研究问题或 coverage shortage 变化时重入，有效既有 evidence 优先复用。

# OPE 证据评审

## 用户价值

形成来源可追溯、筛选理由清楚、覆盖边界明确的证据评审结果。

## Input
- `question_formulation`

## Output
- `evidence_review`

## Acceptance
- `source_identity_and_locators_traceable`
- `screening_reasons_explicit`
- `deep_read_evidence_rows_source_grounded`
- `ope_comparability_fields_and_coverage_explicit`
- `zero_hit_and_partial_read_preserved`
- `unresolved_gaps_and_information_limits_explicit`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`retrieval_boundary`、`seed_sources` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

0 hit、无法抓取和 partial read 都是合法 coverage 结果；不得虚构论文或证据。

## 来源与重入边界

query→source→screen→evidence rows 保持完整 lineage；研究问题、gap 或 coverage shortage 变化时重入。

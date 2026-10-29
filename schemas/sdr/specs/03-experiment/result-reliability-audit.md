# SDR 结果可靠性审计

## 用户价值

独立检查结果的追溯覆盖、缺失、歧义和复现阻断，给出条件性可靠性判断。

## Input
- `result_analysis`
- `supporting_records`
- `material_manifest`

## Output
- `result_reliability_audit`

## Acceptance
- `trace_coverage_and_missing_records_explicit`
- `ambiguities_and_reproducibility_blockers_explicit`
- `verdict_conditioned_on_available_materials`
- `no_inspected_records_yields_insufficient_materials`
- `missing_raw_or_metric_definitions_cannot_pass`
- `needs_revision_needs_input_and_external_unknown_preserved`
- `domain_specific_comparison_rules_not_generalized`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`findings_synthesis`、`metric_definitions` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

无 inspected records 时输出 insufficient_materials；缺 raw / metric definitions 时不得给 pass。

## 来源与重入边界

审计结果引用全部 inspected artifact/material identities；被审计结果或补充记录变化时重入。

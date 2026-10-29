# 证据获取设计

## 输入
- `problem_framing`

## 可选输入
已有且当前适用的来源或证据可用于识别覆盖缺口并避免重复设计；缺失时不猜来源范围、检索词或证据字段。

## 输出
- `evidence_acquisition_design`

## 验收
- `retrieval_matrix_preserves_problem_semantics`
- `source_scope_and_priority_explicit`
- `evidence_schema_fields_and_applicability_explicit`
- `coverage_stop_and_shortage_rules_explicit`
- `retrieval_budget_not_used_as_research_completion_criterion`
- `unknowns_and_missing_design_inputs_preserved`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
基于 `problem_framing` 一次形成证据获取设计：明确检索矩阵、来源范围与优先级、EvidenceSchema，以及覆盖、停止和证据短缺规则。已有且当前适用的来源或证据可以作为设计参考，但不是必需直接输入；信息不足时保留缺口或请求补充，不猜检索词、来源范围或证据字段。预算与轮次只约束获取过程，不能替代科研证据充分性判断。本职责只做设计，不执行外部读取。

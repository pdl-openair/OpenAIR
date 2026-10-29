# 证据获取

## 输入
- `evidence_acquisition_design`
- `current_judgment_target`

## 可选输入
已有且当前适用的来源、ResearchRecord 或 Evidence 优先复用；它们不是 required input，且不得因存在历史结果而跳过当前适用性检查。

## 输出
- `evidence_acquisition`

## 验收
- `source_identity_and_coverage_traceable`
- `screening_decisions_and_unassessed_items_preserved`
- `paper_packets_keep_source_anchors`
- `research_records_and_evidence_are_traceable_to_sources`
- `single_source_failures_are_locally_isolated`
- `zero_results_partial_reads_and_extraction_failures_are_preserved`
- `budget_stop_not_claimed_as_evidence_sufficiency`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
按照 `evidence_acquisition_design` 和当前判断目标获取、筛选、阅读并抽取带来源锚点的研究材料。已有且当前适用的来源或记录优先复用，不要求重复获取。输出保留候选来源与覆盖情况、筛选决定、paper packets、抽取的 ResearchRecords/Evidence 和局部失败。0 命中、部分读取和抽取失败均是合法可追溯结果；不得伪造来源，也不得因预算停止而宣称证据已经充分。本职责最低需要 `external_read`，具体动作接线留给后续 Binding / External Action 施工，不授权写入或物理动作，也不替代 OT v3 的 `research_record_establishment`。

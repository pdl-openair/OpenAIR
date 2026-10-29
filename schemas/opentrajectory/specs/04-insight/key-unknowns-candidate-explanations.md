# 关键未知与候选解释

## 输入
- `current_research_conclusion`
- `material_conflicts_limitations_and_evidence_gaps`

## 输出
- `key_unknowns_and_candidate_explanations`

## 验收
- `unknowns_materially_affect_judgment_or_action`
- `affected_judgment_and_missing_information_explicit`
- `priority_reason_explicit`
- `candidate_explanations_only_when_supported_by_reasonable_basis`
- `discriminating_future_results_explicit_when_candidates_exist`
- `no_forced_hypothesis_when_basis_is_insufficient`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
只保留会实质改变当前结论或用户下一步行动的关键未知，并说明其影响哪个判断、缺少什么信息以及优先处理理由。有合理依据时形成候选解释、假说或判别预测，同时保留提出依据、验证缺口以及哪些新结果会支持、削弱或仍无法区分这些解释；依据不足时只保留未知，不强制生成假说。

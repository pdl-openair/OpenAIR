# 机制解释

## 输入
- `phenomenon_to_explain`
- `relevant_research_records`
- `accepted_evidence_assessment`

## 可选输入
已有且当前适用的 `candidate_explanations_if_any` 可作为候选解释输入；缺失时不强制生成假说。

## 输出
- `mechanism_explanation`

## 验收
- `supported_author_interpretation_and_ot_inference_distinguished`
- `mechanism_paths_grounded_in_available_evidence`
- `unsupported_links_not_fabricated`
- `material_conflicts_and_unverified_relations_explicit`
- `mechanism_unknown_preserved_when_evidence_is_insufficient`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
仅在用户核心问题需要回答“为什么”，或机制解释会实质影响证据解读、当前结论、关键未知或下一步建议时进入。形成可能的作用路径，说明支持或限制这些路径的证据和尚未验证的关系；已有证据支持、作者解释和 OT 推断分别标明，不为补全机制链增加无依据连接。证据不足时保留机制未知，能力暂不可用不等于机制问题已经解决。

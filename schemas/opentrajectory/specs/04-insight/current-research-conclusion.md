# 当前研究结论

## 输入
- `current_research_question`
- `accepted_evidence_assessment_and_findings`

## 可选输入
已接受且当前适用的 `accepted_mechanism_explanation_if_applicable` 可作为补充依据；机制职责保持条件性，不改变本节点 required dependency。

## 输出
- `current_research_conclusion`

## 验收
- `direct_answer_to_main_question_present`
- `each_key_judgment_keeps_basis_and_scope`
- `material_conflicts_limitations_and_uncertainty_explicit`
- `conclusion_strength_not_above_evidence_strength`
- `indeterminate_is_preserved_when_required`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 研究工作说明
直接回答用户主问题，并为每个关键判断保留依据、适用条件、重要冲突、限制和不确定性。不同判断分别表达证据强度，结论强度不超过对应证据。当前无法确定是合法结果，应明确不能确定的具体问题和原因；既有结论只有在当前目标、条件和新增证据检查通过后才复用。

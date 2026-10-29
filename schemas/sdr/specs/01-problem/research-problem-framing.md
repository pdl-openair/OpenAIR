# SDR 研究问题建模

## 用户价值

一次形成通用科研目标、约束、问题、未知和条件性假说，作为后续研究 Authority。

## Input
- `user_goal`

## Output
- `research_problem_framing`

## Acceptance
- `goal_scope_and_success_criteria_explicit`
- `known_constraints_preserved_without_invention`
- `research_questions_specific_and_researchable`
- `assumptions_unknowns_and_clarification_needs_explicit`
- `hypotheses_falsifiable_when_present`
- `hypothesis_generation_not_forced`
- `source_and_version_lineage_preserved`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`known_constraints`、`supplied_evidence`、`advisor_feedback` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

信息不足时返回 needs_input / unknown；不需要假说的研究可合法不生成假说。

## 来源与重入边界

约束、研究问题和可选 hypothesis 保留 user/evidence source 与版本；目标、约束或证据变化到改变问题模型时重入。

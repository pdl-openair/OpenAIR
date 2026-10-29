# Problem Framing

## 目标

一次完成 OpenTrajectory 的正式问题建模，回答：

> 现在研究什么，以及后续根据什么形成判断？

## 输入
- `canonical_user_query`

## 输出
- `problem_framing`

## 科研规则

- 保留用户原始事实，不补造阈值、材料、方法、benchmark、数据库或实验条件。
- 明确区分硬约束、软偏好、显式未知、显式不限、歧义和冲突。
- 未知保持未知；显式不限保持不限；冲突保持冲突，不替用户静默选边。
- 用正式 `semantic_state` 表达确认、未知、不限、歧义和冲突；用 `missing_information` 与 `research_risks` 保留真实专业缺口和风险。
- 不输出模型 `confidence`、`coverage_level`、人工复核声明或其他工作流路由状态；正式 gate 与路由由代码从专业字段确定。
- 等价单位、自然语言方向和科研语义由模型解释，代码只负责结构与合同。
- 可形成直接的检索提示词/过滤提示，但完整检索矩阵、证据问题拆分、证据结构和检索优先级属于 `evidence_acquisition_design`。

## 验收
- `research_objective_and_constraints_complete`
- `explicit_constraints_preserved`
- `unknown_unbounded_conflict_and_ambiguity_preserved`
- `usable_as_evidence_design_upstream`
- `professional_semantics_explicit_without_model_self_evaluation`
- `routing_derived_deterministically_from_formal_state`
- `retrieval_design_not_performed_early`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

# PVD 证据适用性与可比性分析

## 用户价值

判断外部文献、不同实验体系、设备、样品或历史条件下的结果能够支持当前实验体系中的哪些具体判断，以及必须保留哪些迁移限制。

## 项目上下文

`PVDProjectContext` 是本轮冻结且可追溯的项目事实包，包含 structured context、recommendation parameter set、run preferences 以及 revision/lineage；本职责消费该事实包，不重复解析用户上下文，也不形成第六个 Workflow 节点。

## Input
- `PVDProjectContext`
- `research_records`
- `support_target`

## Output
- `evidence_applicability`

## Acceptance
- `each_applicability_judgment_has_specific_downstream_use`
- `directional_evidence_not_promoted_to_specific_parameter_values`
- `comparability_and_transfer_limits_explicit`
- `conflicts_and_important_unknowns_preserved`
- `no_candidate_generation_or_final_recommendation`
- `no_evidence_acquisition_or_raw_material_assembly`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

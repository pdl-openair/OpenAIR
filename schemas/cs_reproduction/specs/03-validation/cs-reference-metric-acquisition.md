# 参考结果获取与结构化

## 输入

- 上游 `repository_revision_acquisition` 已验证的 `code_source` Artifact
- `research_goal`

## 输出

- `reference_metric_records`
- `reference_metric_comparison_requests`
- `reference_metric_source_conflicts`
- `reference_metric_unresolved`

## 验收

- `reference_source_traceable`
- `reference_actual_independent`
- `metric_identity_condition_matched`
- `comparison_rule_source_grounded`
- `unfounded_tolerance_absent`

## 研究工作说明

复用本次复现已经真实取得并固定到 immutable commit 的官方代码仓库工作区，重新核验其 HEAD 与上游正式 Artifact 一致后，读取官方文字来源；随后判断与当前复现目标严格对应的实验设置、指标和 reference value，并形成可追溯的正式 reference facts。

来源读取不再重复访问外网，也不重新选择 repository/revision；实验条件对应、指标语义和来源是否足以支持某个 comparison rule 由 LLM 判断；来源身份、原文引用、stable ID、冲突保留和 comparison request 装配由代码确定性完成。

reference 生产链不得读取 actual metrics。来源没有给出 tolerance 时不得补造 tolerance；来源不能支持正式 comparison rule 时保留缺口，不产生可比较请求。

---
name: reference-metric-fact-assembly
description: 确定性核验 reference 语义草案中的文档身份、原文引用、值类型和 comparison rule，把可信 reference facts 组装为 ScientificState 可保存记录和现有 reference-metric-comparison 可直接消费的请求。
---

# Reference facts 确定性组装

## 职责

本 Skill 不做新的科研判断。它只对上一步已经形成的语义判断做确定性核验与装配：

1. `source_document_id` 必须真实存在于冻结来源包；
2. reference 原文片段和 comparison rule 依据必须逐字存在于对应官方文档；
3. 根据真实原文位置计算行号；
4. 检查 reference value 与声明类型一致；
5. 检查 comparison rule 结构合法，任何 tolerance 必须显式存在于已核验的规则依据中；
6. 生成稳定 `reference_id`；
7. 保留完整 reference records、来源冲突和 unresolved；
8. 只有来源与规则都通过核验、且同一 metric identity 没有冲突时，才生成 `reference_metric_comparison_requests`。

## 独立性

本 Skill 不接收 actual metrics。reference facts 的生成链与实际实验结果完全独立。

## 冲突

同一 `metric_name` 出现不一致的 value / type / unit / comparison rule 时：

- 所有来源记录继续保留；
- 写入 `reference_metric_source_conflicts`；
- 不向比较器输出该指标请求；
- 不由代码选择“更可信”的一个。

## 输出 Authority

本 Skill 的完整 Artifact 保留来源和引用；Registry 只把正式 reference records、comparison requests、冲突和缺口投影到既有 ScientificState。Binding 继续只负责读取这些正式事实，不生产事实。

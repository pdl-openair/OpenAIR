---
name: reproduction-conclusion-closure
description: 确定性保留 B05 三态、真实执行状态与正式证据身份，闭合最终复现结论。
---

# 复现结论确定性收口

本 Skill 不做科研语义判断。最终 `verdict` 只由正式 `reproduction_comparison_judgment.overall_verdict` 映射；正式 comparison 原样深复制并固定标记来源为 `reproduction_comparison_judgment`；`execution_status` 只由正式 `reproduction_execution_record.status` 投影。已确定的三态、comparability 和执行事实不得被综合草稿或诊断改写。

证据充分性按正式结构收口：三态为达标/未达标时为 `sufficient`；三态为 `indeterminate` 且仍存在可用正式执行、实测或比较材料时为 `partial`；缺乏可用正式材料时为 `insufficient`。

Evidence Catalog 只从以下显式字段建立身份：comparison 顶层 `evidence_refs`、正式 `comparability.conditions.*.evidence_refs`、`actual_metric_records.execution_record_ref`、正式诊断 `evidence_supported_causes[*].evidence_refs`。不得递归扫描字符串；状态、错误码、正文、step id 和 diagnosis `validation_id` 都不是 Evidence。

B07 草稿按 answer、scope、单项 limitation、单项 unknown 和单项 next step 独立接受。answer 必须有非空正文并绑定至少一个 Catalog ref；其他叙述片段可以使用空引用数组，但一旦声明引用就必须全部在 Catalog 中。坏片段只进入 `isolated_synthesis_segments`，不影响 B05 或其他片段；缺失或坏草稿形成 `synthesis_gaps`，仍输出 B05-only 结论，不由代码补写叙述。

上游 comparison、execution、actual 和 diagnosis 的正式 unknowns 放入独立的 `upstream_unknowns`，保留来源且不获得 Evidence 身份。下一步只能指向已确认 CS Definition；存在 `source_validation_id` 时必须逐条引用正式 diagnosis `validation_actions[].validation_id`。

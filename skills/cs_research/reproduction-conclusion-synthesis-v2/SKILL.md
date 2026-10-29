---
name: reproduction-conclusion-synthesis-v2
description: 基于正式复现事实形成范围、解释、限制、未知与下一步草稿；不生成或改写最终三态和执行状态。
---

# 复现结论专业综合草稿

只使用正式输入形成用户可理解的结论草稿。`reproduction_comparison_judgment.overall_verdict` 是最终科研三态的唯一 Authority，本 Skill 不输出 `verdict`、`comparability`；`reproduction_execution_record.status` 是程序执行状态的唯一 Authority，本 Skill 不输出 `execution_status`。

`scope` 必须来自正式基线与实验设计，不扩大到未执行或未比较的论文范围。`answer` 直接回答用户当前复现问题，但不得把 `indeterminate` 写成成功或失败，也不得把局部达标扩写为整篇论文复现成功。

`answer`、`scope` 以及每一项 `limitations`、`unknowns` 都是独立片段，结构为 `{text, evidence_refs}`。每个片段只绑定直接支撑自身的引用，不使用全局引用列表。answer 至少绑定一个引用；scope、单项 limitation 和单项 unknown 可以使用空引用数组。引用只能复制正式比较、实际指标执行记录或正式诊断已证实原因中明确声明的 Evidence 身份，不得复制状态、错误码、validation_id、普通文本或自行创建引用。

若存在 `reproduction_deviation_diagnosis`：
- 只有 `evidence_supported_causes` 可作为已证实解释；
- `hypotheses` 始终保持待验证身份，只能进入 `unknowns` 或验证建议背景；
- 尚未执行的 `validation_actions` 只能作为 `next_steps`；
- `entry_assessment.status=not_applicable` 时不得从诊断生成科研根因；
- `closure.can_close_with_limitations=true` 且没有有价值验证时，可以给出空 `next_steps` 并带限制收口。

每个 `next_steps` 必须指向当前 CS Workflow 中一个正式 Definition，并说明它预期改变哪个判断；需要改变已确认 ResearchDesign 时设置 `requires_design_change=true`。没有值得继续的动作时输出空数组。

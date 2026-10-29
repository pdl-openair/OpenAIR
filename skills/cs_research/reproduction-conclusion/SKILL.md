---
name: reproduction-conclusion
description: 基于正式可比性与达标判断，直接回答当前范围是否复现并给出有限下一步。
---

# 复现结论与下一步建议

结论范围必须与正式 `reproduction_comparison_judgment` 完全一致。分别表达程序执行状态、证据充分性和科研达标判断；`indeterminate` 不得改写为成功或失败。可选的 `reproduction_deviation_diagnosis` 只用于解释已证实原因、限制、剩余未知和下一步，不得改变比较层已经形成的三态结论。

只有诊断中的 `evidence_supported_causes` 可以作为已证实解释使用，并保留其证据引用。`hypotheses` 始终保持待验证身份，不得在 answer、limitations 或其他字段中升级为事实；尚未执行的 `validation_actions` 只能作为 next steps，不得描述为已经验证。`entry_assessment.status=not_applicable` 的诊断不提供科研根因。

部分实验成功不扩写为整篇论文复现成功。下一步只保留可能改变判断或关闭当前范围的动作，不新增未经授权的研究目标；若诊断已明确 `can_close_with_limitations=true` 且没有有价值验证，不为流程完整继续追加实验。

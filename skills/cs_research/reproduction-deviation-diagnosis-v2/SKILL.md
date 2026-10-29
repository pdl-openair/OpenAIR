---
name: reproduction-deviation-diagnosis-v2
description: 对可能改变复现结论的偏差做进入判定、证据分层诊断，并形成可由后续 ExecutionDecision 路由的验证要求。
---

# 复现偏差诊断与验证路径

先判断本次是否满足正式进入条件：未达标、异常高分、运行间差异、来源冲突或执行偏离，并且该问题可能实质改变复现结论或下一步行动。若唯一问题只是网络、文件、进程等一般运行故障，返回 `not_applicable`，不得生成科研根因或假设。

只把输入中已有证据直接支持的解释放入 `evidence_supported_causes`，每项必须给出证据引用。合理但未经验证的解释只放入 `hypotheses`；没有足够证据时保持未知。

只有验证具有区分价值时才生成 `validation_actions`。每项验证必须：
- 对应一个或多个待验证假设；
- 指向当前已确认 CS Workflow 中受影响的正式 Definition；
- 说明至少两种不同结果；
- 对每种结果说明会改变哪个判断以及怎样改变；
- 明确是否需要 ResearchDesign 变更。

`validation_actions` 是后续 ExecutionDecision 可消费的正式研究/执行要求，不代表验证已经执行。不得生成任意 shell 命令、虚构运行结果或绕过 ResearchDesign 的新任务。需要新增研究任务或改变已确认边界时只标记 `requires_design_change=true`。

没有进一步验证价值时，允许 `validation_needed=false` 且 `can_close_with_limitations=true`，保留剩余未知。

---
name: reproduction-comparability-assessment
description: 判断论文参考结果与本次真实复现结果是否具有比较资格；只负责科研可比性，不输出达标结论。
---

# 复现结果可比性判断

逐项核验任务范围、实现、数据划分、预处理、评测协议、指标口径、随机性和硬件环境。每一类条件都必须给出 `compatible`、`mismatch` 或 `unknown`，并保留直接证据引用。

只判断“能不能比”，不得计算阈值、差值、容差，不得输出 `meets_standard` 或 `does_not_meet_standard`。存在条件不一致时为 `not_comparable`；证据不足或关键条件未知时为 `indeterminate`；只有八类条件都具有充分证据且兼容时才可为 `comparable`。

---
name: reproduction-comparison-judgment
description: 先核验执行与参考条件可比性，再依据预先确定的规则和确定性比较形成达标判断。
---

# 复现结果可比性与达标判断

先逐项检查任务范围、实现、数据划分、预处理、协议、指标口径、随机性和硬件条件。只有可比且存在预先有依据的比较规则时，才能采用 `deterministic_comparison` 的结果形成达标判断。

不得自行重算或修改 actual/reference 值，不得根据结果修改 tolerance。条件不足、规则缺失或证据冲突时输出 `indeterminate` 并保留差异。

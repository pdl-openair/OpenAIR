---
name: reproduction-comparison-closure
description: 将可比性判断与确定性指标比较闭合为最终三态复现结论。
---

# 复现比较三态收口

本 Skill 只做确定性收口，不执行科研语义判断，不修改 actual/reference 值或判据，也不按未知文本判断科学重要性。

仅当可比性为 `comparable`、八类比较条件均有证据且为 `compatible`、不存在 `condition_mismatches`，并且确定性比较完整成功时，最终结果才可为 `meets_standard` 或 `does_not_meet_standard`。这个结论只覆盖已声明的比较范围，不能据此声称论文整体复现通过。

顶层 `unknowns` 不推翻上述正式可比判断，并原样保留在输出中。assessment 为 `indeterminate` / `not_comparable`、任一关键条件为 `unknown` 或 `mismatch`、缺少证据，或确定性比较未完整成功时，最终结果为 `indeterminate`。条件不一致、未知项和确定性比较失败信息保持可读。局部指标满足规则不能写成整体复现成功。

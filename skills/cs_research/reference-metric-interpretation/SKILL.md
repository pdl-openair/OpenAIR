---
name: reference-metric-interpretation
description: 基于已固定的官方来源包和当前复现研究目标，判断哪些官方结果与本次复现实验条件严格对应，并形成待确定性核验的 reference metric 语义草案；无依据时保持 unknown，不发明 tolerance。
---

# 论文复现参考指标语义判断

## 任务

输入是：

- 当前已确认的 `research_goal`；
- 已由系统真实获取并固定身份的官方 `source_package`。

你只负责科研语义判断：

1. 判断官方来源中的哪个实验 / 模型 / 数据集 / 评测设置与当前复现目标对应；
2. 确认指标名称、数值、单位和关键实验条件；
3. 指向支持该 reference value 的**原文片段**；
4. 判断来源是否明确给出了可用于机械比较的规则依据。

## 指标身份

`metric_name` 必须使用官方结果表、程序输出或正式评测定义中实际使用、且能与本次复现输出建立精确身份对应的名称。

不要根据 actual metrics 反推或改名；本 Skill 不接收 actual metrics。

## comparison rule

只允许在来源有正式依据时给出规则：

- `exact`：来源把该数值明确作为匹配条件下应得到的确定结果 / expected output；
- `absolute_tolerance`：来源明确给出绝对误差允许值；
- `relative_tolerance`：来源明确给出相对误差比例；
- `actual_gte_reference`：来源明确把 reference 表述为下限 / 至少达到；
- `actual_lte_reference`：来源明确把 reference 表述为上限 / 不得超过。

**禁止因为“复现实验通常会有波动”自行创造 tolerance。**

若来源只报告一个论文结果，但没有说明允许偏差、阈值或 expected output 语义，则 `comparison_rule=null`，并在 `unresolved` 说明缺少正式比较规则依据。

任何 tolerance 都必须在 `comparison_rule_basis_excerpt` 中存在可核验的明确数值依据；否则不得输出 tolerance。

## 来源引用

- `source_document_id` 必须逐字使用输入来源包中的现有 ID；
- `source_excerpt` 必须逐字摘自该文档，覆盖 reference value 及其指标/实验上下文；优先给出足以独立核验的最小连续片段，而不是整段文档。
- `comparison_rule_basis_excerpt` 若非 null，也必须逐字摘自同一文档并足以解释规则来源；同样应使用最小连续片段。

两类原文摘录各最多 4096 个字符。这是承载完整表格、相邻条件或正式规则说明的容量边界，不改变“逐字可核验”的要求。

后续确定性程序会重新核验文档 ID 与原文片段；无法逐字定位的候选不会进入正式 reference facts。

## 冲突与缺失

- 多个官方结果对应不同模型、数据集、模式或版本时，保持各自条件，不合并；
- 来源之间冲突时保留冲突，不擅自选一个；
- 当前材料不足以确认实验条件对应关系时，不生成可比较 reference，写入 `unresolved`；
- 不补造未报告条件、单位、误差或 tolerance。

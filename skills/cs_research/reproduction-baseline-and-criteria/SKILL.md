---
name: reproduction-baseline-and-criteria
description: 基于已确认复现目标、论文信息与执行约束，形成复现基线与验收标准；保持未知，不补造实验条件。
---

# 复现基线与验收标准

## 职责

- 明确本次复现需要对齐的目标、基线、比较对象和验收条件。
- 输出可追溯的复现判断依据。
- 缺失信息保持未知，不推断论文未公开条件。
- `reference_facts` 是参考值、比较规则、来源冲突和未解决项的唯一正式事实输入；存在时逐项保留其值、类型、单位、实验条件、来源身份和规则，不改写或补充事实。
- `reference_sources` 只从 `reference_facts.reference_metric_records[].source` 汇总，不从模型记忆或未核验的研究目标生成。
- 只有输入中可追溯的来源才能进入 `reference_sources`；不得把常识或模型记忆伪装成来源。
- 来源没有明确规定比较规则时，不生成 `reference_metric_comparison_requests`，并在 `unknowns` 中保留缺口。
- `reference_facts` 缺失、`assembly_status=incomplete` 或正式来源不足时，输出 `status=incomplete`，保留对应 `unknowns`，不得从 `research_goal` 补造参考值或容差。
- `status=complete` 仅表示目标、条件、正式参考值、可追溯来源和有依据的比较规则足以支持本次复现判断。

## 边界

- 不执行代码。
- 不替代环境准备、指标提取或结果比较。
- 不读取或使用 actual metrics，不根据实际结果选择参考值或修改判据。
- 不生成未经证据支持的实验结论。

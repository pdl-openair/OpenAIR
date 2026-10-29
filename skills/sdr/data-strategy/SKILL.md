---
name: data-strategy
description: 确定数据来源、采集方法、样本量论证
---

# 数据策略 (L2)

## Goal
确定数据来源、采集方法、样本量论证

## Must Do
1. 确定数据来源（公开数据集/自采/合成）与采集协议
2. 论证样本量与统计功效
3. 输出 data-strategy spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/02-method/data-strategy.md` 模板产出，使用 Write 工具写入当前任务工作区的 `02-method/data-strategy.md`。

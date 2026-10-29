---
name: baseline-selection
description: 选择对比方法，确认来源和兼容性
---

# 基线/竞品选择 (L2)

## Goal
选择对比方法，确认来源和兼容性

## Must Do
1. 检索并选择领域内代表性基线方法
2. 确认基线代码/模型的可获取性与兼容性
3. 输出 baselines spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/02-method/baselines.md` 模板产出，使用 Write 工具写入当前任务工作区的 `02-method/baselines.md`。

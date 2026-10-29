---
name: exploratory-execution
description: 最小验证、错误分析、计划可行性检查
---

# 探索执行 (L2)

## Goal
最小验证、错误分析、计划可行性检查

## Must Do
1. 在小规模数据上执行最小验证实验
2. 分析错误模式，评估计划可行性
3. 输出 execution-exploratory spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/03-experiment/execution-exploratory.md` 模板产出，使用 Write 工具写入当前任务工作区的 `03-experiment/execution-exploratory.md`。

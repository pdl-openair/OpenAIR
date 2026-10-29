---
name: hypothesis-validation
description: 将发现映射回假设，判定结论
---

# 假设验证 (L2)

## Goal
将发现映射回假设，判定结论

## Must Do
1. 将统计分析结果映射回每条假设
2. 判定每条假设的支撑状态（支持/拒绝/待定）
3. 输出 hypothesis-validation spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/03-experiment/hypothesis-validation.md` 模板产出，使用 Write 工具写入当前任务工作区的 `03-experiment/hypothesis-validation.md`。

---
name: result-acceptance
description: 最终质量与追溯完整性检查
---

# 结果验收 (L2)

## Goal
最终质量与追溯完整性检查

## Must Do
1. 执行最终质量检查（数据完整性、追溯覆盖）
2. 验收所有 spec 产物的追溯 ID 一致性
3. 输出 result-acceptance spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/03-experiment/result-acceptance.md` 模板产出，使用 Write 工具写入当前任务工作区的 `03-experiment/result-acceptance.md`。

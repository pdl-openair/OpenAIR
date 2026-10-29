---
name: negative-result-recording
description: 记录失败实验，禁止静默丢弃
---

# 负面结果记录 (L2)

## Goal
记录失败实验，禁止静默丢弃

## Must Do
1. 记录所有失败或非预期实验结果
2. 确保负面结果纳入追溯体系，禁止静默丢弃
3. 输出 negative-results spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/03-experiment/negative-results.md` 模板产出，使用 Write 工具写入当前任务工作区的 `03-experiment/negative-results.md`。

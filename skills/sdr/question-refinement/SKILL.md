---
name: question-refinement
description: 将空缺转化为具体可研究的问题
---

# 问题凝练 (L2)

## Goal
将空缺转化为具体可研究的问题

## Must Do
1. 将高置信度空缺转化为明确、可操作的研究问题
2. 确保问题具备可验证性和范围合理性
3. 输出 research-questions spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/01-problem/research-questions.md` 模板产出，使用 Write 工具写入当前任务工作区的 `01-problem/research-questions.md`。

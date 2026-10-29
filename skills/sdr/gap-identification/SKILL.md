---
name: gap-identification
description: 从文献中识别未解决问题，标注置信度
---

# 空缺识别 (L2)

## Goal
从文献中识别未解决问题，标注置信度

## Must Do
1. 对比文献综述，识别尚未解决的问题或方法空白
2. 为每个空缺标注置信度与证据等级
3. 输出 gap-analysis spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/01-problem/gap-analysis.md` 模板产出，使用 Write 工具写入当前任务工作区的 `01-problem/gap-analysis.md`。

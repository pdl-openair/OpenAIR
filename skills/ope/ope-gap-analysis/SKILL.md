---
name: ope-gap-analysis
description: 识别研究空缺，评估置信度
---

# 空缺分析 (L2 — OPE)

## Goal
基于文献综述识别研究空缺，标注置信度。

## Must Do
1. 对比文献综述，识别尚未解决的问题或方法空白
2. 为每个空缺标注置信度与证据等级
3. 输出 analysis spec

## Must NOT
- 不得跳过前序 substage 的依赖

## Output Contract
按 `schemas/ope/specs/analysis.md` 模板产出，使用 Write 工具写入当前任务工作区的 `proposal/analysis.md`。

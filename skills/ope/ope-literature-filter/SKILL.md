---
name: ope-literature-filter
description: 筛选高质量文献，形成文献综述
---

# 文献筛选 (L2 — OPE)

## Goal
对检索结果进行质量筛选，形成结构化文献综述。

## Must Do
1. 按相关性、发表年份、引用量等维度筛选文献
2. 对每篇保留文献提取核心贡献与局限
3. 输出文献综述报告

## Must NOT
- 不得跳过质量评估环节
- 不得在筛选时引入主观偏见

## Output Contract
按 `schemas/ope/specs/literature.md` 模板产出，使用 Write 工具写入当前任务工作区的 `proposal/literature.md`。

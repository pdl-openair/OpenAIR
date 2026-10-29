---
name: report-generation
description: 生成论文、图表、报告片段
---

# 报告/图表生成 (L2)

## Goal
生成论文、图表、报告片段

## Must Do
1. 按目标格式生成论文片段、图表和报告
2. 确保所有陈述有追溯证据支撑
3. 输出 report spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/04-report/report.md` 模板产出，使用 Write 工具写入当前任务工作区的 `04-report/report.md`。

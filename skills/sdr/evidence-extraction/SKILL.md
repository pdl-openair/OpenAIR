---
name: evidence-extraction
description: 从原始结果提取结构化证据
---

# 证据抽取 (L2)

## Goal
从原始结果提取结构化证据

## Must Do
1. 从原始实验输出中提取结构化指标
2. 标注每个证据的追溯链路
3. 输出 evidence-extraction spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/03-experiment/evidence-extraction.md` 模板产出，使用 Write 工具写入当前任务工作区的 `03-experiment/evidence-extraction.md`。

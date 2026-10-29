---
name: ope-info-retrieval
description: 检索相关文献与信息，构建参考文献库
---

# 信息检索 (L2 — OPE)

## Goal
围绕研究主题检索相关文献与信息，构建结构化参考文献库。

## Must Do
1. 根据研究主题提取检索关键词与检索策略
2. 调用学术检索接口获取相关文献
3. 去重、标准化引用格式，输出参考文献清单

## Must NOT
- 不得跳过关键词提取步骤直接检索
- 不得遗漏检索来源说明

## Output Contract
按 `schemas/ope/specs/reference.md` 模板产出，使用 Write 工具写入当前任务工作区的 `proposal/reference.md`。

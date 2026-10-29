---
name: literature-search
description: 广度搜索、全文获取、深度阅读
---

# 文献检索 (L2)

## Goal
广度搜索、全文获取、深度阅读

## Must Do
1. 按关键词与领域进行广度优先的文献检索
2. 获取并解析全文内容
3. 输出带追溯信息的 literature-review spec
4. 所有论文条目必须保留 DOI / URL / arXiv / PMID 等来源 ID；没有拿到时写 `待补 DOI`，不得省略该列或编造 DOI

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec
- 不得在总结或压缩输出时删除已有 DOI / URL / arXiv / PMID

## Output Contract
按 `schemas/sdr/specs/01-problem/literature-review.md` 模板产出，使用 Write 工具写入当前任务工作区的 `01-problem/literature-review.md`。

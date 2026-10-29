---
name: candidate-dedup-and-provenance
description: >-
  对多源检索返回的元数据候选集进行去重，保留最优版本（按引用数、年份、arXiv PDF 优先级），
  合并 provenance 标记每条文献被多少个检索源命中。
  在 academic-source-retrieval-skill 之后、candidate-source-scoring 之前运行。
---

# Candidate Dedup and Provenance

## 概述

**候选文献去重与来源标记** Skill 负责对多源检索返回的元数据候选集进行去重，保留最优版本，并标记每条文献被多少个检索源命中（provenance）。

## 能力边界

### 做什么
- 按 `dedupe_key`（DOI / arXiv ID / OpenAlex ID / normalized_title）去重
- 保留引用数更高或年份更新的版本
- 合并 provenance：记录每条文献被哪些检索源、哪些查询命中
- 统计多源命中数（`matched_source_count`）
- 生成去重报告（输入/输出数量、去重键分布、多源候选数）

### 不做什么
- ❌ 不做质量评分（relevance/citations/recency scoring）
- ❌ 不做分层（Tier1/Tier2/Tier3）
- ❌ 不做 Top-K 选择
- ❌ 不访问全文 PDF
- ❌ 不调用 LLM
- ❌ 不依赖数据库、API、任务状态机

## 输入

```json
{
  "metadata_candidates": [
    {
      "candidate_id": "openalex_W1234567890",
      "dedupe_key": "doi:10.1016/j.jpowsour.2023.233456",
      "normalized_title": "fast charging lithium ion batteries",
      "external_ids": {
        "doi": "10.1016/j.jpowsour.2023.233456",
        "arxiv_id": null,
        "openalex_id": "W1234567890"
      },
      "title": "Fast Charging Lithium-Ion Batteries",
      "abstract": "...",
      "authors": ["Zhang, Wei", "Li, Ming"],
      "year": 2023,
      "venue": "Journal of Power Sources",
      "doi": "10.1016/j.jpowsour.2023.233456",
      "citation_count": 45,
      "source_name": "openalex",
      "matched_query": "fast charging lithium ion battery",
      "provenance": [
        {
          "source_name": "openalex",
          "matched_query": "fast charging lithium ion battery",
          "query_index": 0,
          "source_result_index": 0,
          "source_rank": 1,
          "retrieved_at": "2026-05-26T12:00:00Z",
          "retrieval_mode": "expanded_query_variant"
        }
      ]
    }
  ]
}
```

**兼容性**：输入 schema 与 `academic-source-retrieval-skill` 输出完全兼容。

## 输出

```json
{
  "deduped_candidates": [
    {
      "candidate_id": "openalex_W1234567890",
      "dedupe_key": "doi:10.1016/j.jpowsour.2023.233456",
      "normalized_title": "fast charging lithium ion batteries",
      "title": "Fast Charging Lithium-Ion Batteries",
      "year": 2023,
      "citation_count": 45,
      "matched_source_count": 2,
      "dedup_reason": "higher_citations",
      "provenance": [
        {
          "source_name": "openalex",
          "matched_query": "fast charging lithium ion battery",
          "retrieved_at": "2026-05-26T12:00:00Z"
        },
        {
          "source_name": "semantic_scholar",
          "matched_query": "rapid charge Li-ion cell",
          "retrieved_at": "2026-05-26T12:01:00Z"
        }
      ]
    }
  ],
  "dedup_report": {
    "input_count": 150,
    "output_count": 87,
    "duplicates_removed": 63,
    "multi_source_candidates": 23,
    "dedup_keys_used": {
      "doi": 45,
      "arxiv_id": 12,
      "openalex_id": 18,
      "normalized_title": 12
    }
  }
}
```

## 去重逻辑

### 1. 去重键优先级

本 Skill 会先规范化外部标识，再按以下优先级生成稳定去重键：

```
dedupe_key = DOI > arXiv ID > existing cross-source title key > OpenAlex ID > legacy dedupe_key > normalized_title hash
```

**说明**：
- 优先读取 `external_ids` / 顶层 metadata 中的强标识；即使上游已经提供旧 `dedupe_key`，新发现的 DOI 仍应覆盖较弱的 arXiv 或标题键
- DOI 统一规范为 `doi:<value>`，arXiv ID 会移除版本后缀
- 兼容旧 `dedupe_key`；没有 DOI / arXiv 时，保留上游已生成的跨来源标题键，再 fallback 到来源专属 OpenAlex ID
- 最后 fallback 到 `normalized_title` 的 hash 值

### 2. 版本选择规则
当多个候选映射到同一 `dedupe_key` 时，按以下规则保留最优版本：

1. **引用数优先**：`citation_count` 更高者
2. **年份优先**：引用数相同时，`year` 更新者
3. **arXiv PDF 优先**：引用数和年份都相同时，优先保留有 arXiv PDF 直链的版本
4. **先到先得**：以上都相同时，保留第一个遇到的版本

### 3. Provenance 合并
- 合并所有重复候选的 `provenance` 数组
- 统计不同 `source_name` 的数量 → `matched_source_count`
- 保留所有查询命中记录（用于后续分析检索覆盖度）

## 迁移来源

**源仓库**：OpenTrajectory
**源文件**：`literature_merge_filter.py`
**源函数**：`merge_candidates()` (L25-57)

**核心逻辑**：
```python
def merge_candidates(groups: list[list[LiteratureCandidate]]) -> list[LiteratureCandidate]:
    best: dict[str, LiteratureCandidate] = {}
    sources_by_key: dict[str, set[str]] = {}

    for group in groups:
        for c in group:
            k = c.dedupe_key.lower()
            sources_by_key.setdefault(k, set()).add(c.source_backend)

            if k not in best:
                best[k] = c
                continue

            o = best[k]
            oc, nc = o.citation_count or 0, c.citation_count or 0
            oy, ny = o.year or 0, c.year or 0

            # 保留引用更多或年份更新的版本
            if nc > oc or (nc == oc and ny > oy):
                best[k] = c
            elif nc == oc and ny == oy and "arxiv.org/pdf" in c.pdf_url:
                best[k] = c

    # 标记多源命中数
    for k, c in best.items():
        n = len(sources_by_key.get(k, {c.source_backend}))
        c.extra["matched_source_count"] = str(n)

    return sorted(best.values(), key=lambda x: (-(x.citation_count or 0), -(x.year or 0)))
```

## 测试覆盖

### Fixture 场景
1. **DOI 重复**：同一 DOI 被 OpenAlex 和 Semantic Scholar 返回，保留引用数更高的版本
2. **标题归一化重复**：无 DOI 的文献，通过 `normalized_title` 去重
3. **多源命中**：同一文献被 3 个检索源命中，`matched_source_count = 3`
4. **年份优先**：引用数相同时，保留更新的版本
5. **arXiv PDF 优先**：引用数和年份都相同时，保留有 arXiv PDF 的版本

### 回放测试
- 单 Skill smoke test（不依赖上下游 Skill）
- 输入：`examples/request.json`（包含上述 5 种场景）
- 输出：`examples/response.json`（验证去重逻辑和 provenance 合并）

## 下游 Skill

本 Skill 的输出可供以下下游 Skill 使用：

- **#9 candidate-source-scoring**：对去重后的候选集进行多维质量评分
- **#10 candidate-tiering**：根据评分结果将候选集分层（Tier1/Tier2/Tier3）
- **#11 topk-source-selection**：从各层中选择 Top-K 候选文献

## 运行时要求

- **Runtime**: `claude_code`（纯逻辑处理，无 LLM 调用）
- **依赖**: 无外部 API、数据库、文件系统依赖
- **幂等性**: 相同输入保证相同输出（排序稳定）

## 状态

- **Status**: `draft`
- **Fixture Status**: `example_only`（Skill 自身保留示例；另已纳入 batch 2A deterministic replay）
- **Owner**: TBD

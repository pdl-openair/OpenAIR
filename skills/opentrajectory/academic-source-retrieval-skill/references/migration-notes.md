# Migration Notes: academic-source-retrieval-skill

## P0.7a 当前状态（2026-06-08）

首个可部署纵向切片已经完成自包含化：

```text
Phase3 retrieval matrix
→ academic-source-retrieval-skill
→ OpenAlex + arXiv 真实 metadata 召回
→ 可追溯 MetadataCandidate[]
```

Runtime 实现位于 `adapter/standalone_sources.py`，不再 import 旧项目目录 `04_backend/app/integrations/*`。Semantic Scholar 保留为可选 best-effort metadata 源；DeepXiv 与 Agentic Data 暂不纳入 P0.7a 部署阻断项。

下文保留 P0 阶段的历史迁移来源，便于追溯，不代表当前 Runtime 仍依赖旧目录。

## 历史代码来源

| 功能 | 源文件路径 |
|------|-----------|
| 多源并发检索编排 | `04_backend/app/services/research_info_retrieval.py`（P-02 metadata 模式部分） |
| OpenAlex 检索 | `04_backend/app/integrations/openalex_search.py` → `search_openalex()` |
| Semantic Scholar 检索 | `04_backend/app/integrations/semantic_scholar.py` → `search_semantic_scholar(require_pdf=False)` |
| arXiv 检索 | `04_backend/app/integrations/arxiv_retrieval.py` → `search_arxiv()` |
| DeepXiv 检索 | `04_backend/app/integrations/deepxiv_search.py` → `search_deepxiv()` |
| 候选数据结构 | `04_backend/app/integrations/literature_models.py` → `LiteratureCandidate` |
| 速率限制 | `04_backend/app/integrations/rate_limiter.py` |

## 复用策略

P0 阶段最初采用 thin adapter；P0.7a 已将首个部署切片改为 Skill 包内自包含 metadata 客户端。

**复用方式：**
- P0.7a：调用 `adapter/standalone_sources.py` 内的自包含客户端
- `LiteratureCandidate` → `MetadataCandidate` 的字段映射（见下方）
- 并发模式参考 `research_info_retrieval.py` 中的 `ThreadPoolExecutor` 模式

**不复用的部分：**
- `retrieve_literature_pdfs_for_task()`：耦合了数据库、PDF 下载、评分排序，不适合直接调用
- `heuristic_literature_filter()`：属于筛选逻辑，本 Skill 不做筛选
- `score_and_rank_candidates()`：属于评分逻辑，本 Skill 不做评分
- `p025_topk_selector.py`：属于 Top-K 选择，本 Skill 不做

## LiteratureCandidate → MetadataCandidate 字段映射

| LiteratureCandidate 字段 | MetadataCandidate 字段 | 说明 |
|---|---|---|
| `dedupe_key` | `dedupe_key` + `candidate_id` 的一部分 | `candidate_id` = `{source_name}:{dedupe_key}:{query_index}` |
| `title` | `title` + `normalized_title` | normalized_title = 小写去标点 |
| `doi` / `arxiv_id` / `openalex_id` 等 | `external_ids` | 汇总所有已知外部 ID |
| `abstract_snippet` | `abstract` | 截断到 800 字符 |
| — | `authors` | S2 返回 authors 字段；arXiv/OpenAlex 需从 raw 提取 |
| `year` | `year` | 直接映射 |
| — | `venue` | S2 返回 venue；其他源从 raw 提取或 null |
| `doi` | `doi` | 直接映射 |
| `arxiv_id` | `arxiv_id` | 直接映射 |
| `pdf_url` | `source_url` | 直接映射（**仅 metadata，不下载，不判断 OA**） |
| — | `landing_page_url` | arXiv abs URL / DOI URL / S2 paper URL |
| `source_backend` | `source_name` | 直接映射 |
| — | `matched_query` | 调用时注入 |
| — | `search_intent` | 来自 query variant |
| — | `query_variant_type` | 来自 query variant |
| — | `provenance[]` | 调用时构建，含 query_index / source_result_index / source_rank / retrieved_at / retrieval_mode |
| `extra` + 完整 API 响应 | `raw_metadata` | 保留原始字段 |

## P0 边界说明（2026-05-24 修订）

### 当前状态

本 Skill 当前为 **OpenAIR Skill contract 基础版（P0）**，包含：
- 完整 input/output schema contract
- examples / replay tests / p0_validation.py
- migration-notes（本文件）和 source-mapping

**尚未包含**：实际调用 OpenTrajectory 检索函数的 adapter / runtime wrapper。实际运行需要后续补充 adapter 代码。

### Query 构建规则（P0 新增）

- **禁止**直接使用中文 `user_query` 作为检索词
- 正式检索必须使用英文 `expanded_query_variants[].query` 或 `search_matrix` 构建的英文 Boolean query
- 只有 `user_query` 本身是英文且无其他 query 来源时，才允许 `english_user_query_fallback`
- 中文 `user_query` + 无英文 variants → 返回空候选 + `risk_flags: ["missing_english_query_variants"]`

### 默认 source 策略（P0 新增）

- 默认启用：`openalex`、`semantic_scholar`、`arxiv`
- `deepxiv` / `agentic_data` 仅在 `target_sources` 显式指定时启用
- 未配置时跳过，不影响 `partial_success`

### pdf_url / source_url 边界（P0 明确）

- `source_url` 和 `raw_metadata` 中的 PDF 链接**仅作为 metadata 保留**
- 不代表已下载、不代表可访问、不代表 OA 状态已判断
- OA / PDF / HTML / XML / TEI 可用性由后续 `source-access-readiness-resolver-skill` 处理

### 下游 dedup 必需字段（P0 新增）

每个 `MetadataCandidate` 新增：
- `dedupe_key`：优先 DOI，其次 arxiv_id，其次 openalex_id，其次 normalized_title hash
- `normalized_title`：小写、去标点的 title
- `external_ids`：doi / arxiv_id / openalex_id / semantic_scholar_id / corpus_id / pmid / pmcid

### provenance 检索路径字段（P0 新增）

每个 `provenance[]` 项新增：
- `query_index`：expanded_query_variants 中的序号（0-based）
- `source_result_index`：该 source 返回中的位置（0-based）
- `source_rank`：1-based rank
- `retrieved_at`：ISO 8601 时间戳
- `retrieval_mode`：`expanded_query_variant` / `search_matrix_fallback` / `english_user_query_fallback`

## 迁移边界

**本 Skill 包含：**
- query variants → 多源并发检索
- `LiteratureCandidate` → `MetadataCandidate` 映射（含 dedupe_key / normalized_title / external_ids）
- 检索失败记录（failed_sources）
- metadata 字段缺失记录（retrieval_warnings）
- retrieval_report 生成

**本 Skill 不包含（由下游 Skill 负责）：**
- 候选去重 → `candidate-dedup-and-provenance`
- OA 可用性判断 → `source-access-readiness-resolver-skill`
- title/abstract 筛选 → `title-abstract-screening`
- 候选评分 → `candidate-source-scoring`
- Top-K 选择 → `topk-source-selection`
- PDF 下载 → 后续 PDF 处理 Skill

## 已知限制（基础版）

1. **authors 字段**：arXiv Atom API 不直接返回 authors 列表，需从 XML 解析；基础版可能返回 `null`
2. **venue 字段**：arXiv 和 OpenAlex 的 venue 信息不统一，基础版可能返回 `null`
3. **DeepXiv 依赖**：需要安装 `deepxiv_sdk` 且配置 token，否则自动跳过
4. **Agentic Data 依赖**：需要配置 token，否则自动跳过
5. **速率限制**：复用 `rate_limiter.py`，但 Skill 运行环境可能没有持久化的速率状态
6. **并发安全**：基础版使用 ThreadPoolExecutor，在某些运行时环境下需注意线程安全

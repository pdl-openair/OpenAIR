---
name: academic-source-retrieval-skill
description: >-
  接收 retrieval-matrix-generation 的 SearchMatrix / expanded_query_variants，
  调用自包含学术 metadata 客户端（OpenAlex、arXiv；Semantic Scholar 为可选 best-effort 源），
  返回原始 MetadataCandidate[]。不做去重、筛选、评分、PDF 下载或全文解析。
  在 retrieval-matrix-generation 之后、candidate-dedup-and-provenance 之前运行。
---

# 学术文献多源 Metadata 召回

你是一个文献检索执行器。你的任务是把上游检索矩阵中的 query variants 分发给多个学术数据源，收集原始 metadata 候选，并如实记录每条候选的来源、query 和检索状态。

## When to use

- Pipeline 已完成 `retrieval-matrix-generation`，输入中存在非空 `expanded_query_variants` 或非空 `search_matrix`。
- 用户明确要求执行文献检索、召回候选文献、获取 metadata 候选池。

## Do NOT use when

- 输入中没有任何英文 query（`expanded_query_variants` 为空/null 且 `search_matrix` 为空/null，且 `user_query` 为中文或空）。
- 用户要求筛选、排序、评分、去重候选文献 → 由 `candidate-dedup-and-provenance`、`title-abstract-screening`、`candidate-source-scoring` 负责。
- 用户要求下载 PDF 或解析全文 → 由后续 skill 负责。
- 用户要求判断 OA 可用性或 HTML/XML/TEI 可访问性 → 由 `source-access-readiness-resolver-skill` 负责。
- 用户要求 Top-K 选择 → 由 `topk-source-selection` 负责。

## Input

必填：
- `user_query`：仅用于 provenance 记录和 fallback 说明，**不得直接作为检索 query**（尤其是中文 user_query）

**至少一个非空（P0 约束）：**
- `expanded_query_variants`：优先使用，每条 variant 对应一次多源检索；**query 字段必须是英文**
- `search_matrix`：fallback，从 `must_include_terms` 构建英文 Boolean query

可选：
- `quality_report`：透传，不影响检索逻辑
- `retrieval_config`：控制检索行为
  - `target_sources`：指定启用的数据源，默认 `["openalex", "semantic_scholar", "arxiv"]`；首个部署 smoke 显式使用 `["openalex", "arxiv"]`，`deepxiv` / `agentic_data` 尚未纳入 P0.7a 自包含 Runtime
  - `max_results_per_query`：每个 query 每个 source 最多返回条数，默认 10
  - `max_total_candidates`：总候选上限，默认 200
  - `timeout_seconds`：单次 API 超时，默认 45
  - `allow_partial_success`：部分 source 失败时是否继续，默认 true

## 英文 Query 输入守门规则（P0.1）

`user_query` 可以是中文，仅用于 provenance 记录和用户原始问题追溯，**不得**用作检索词。

正式检索 query 必须来自以下英文字段：
- `expanded_query_variants[].query`
- `search_matrix[].query_terms` / `must_include_terms` / `optional_terms` / `exclude_terms`

**如果上述任意字段含有 CJK 字符（㐀–鿿 等），则：**
- 不得执行检索
- 返回空候选：`metadata_candidates: []`
- `retrieval_report.fallback_used: true`
- `retrieval_report.total_candidates: 0`
- `retrieval_report.risk_flags` 必须包含 `non_english_query_terms`

**不要**尝试自动翻译中文 query。翻译应由上游 Skill 1 / Skill 2（`structured-requirement-generation` / `retrieval-matrix-generation`）完成。

## Query 构建规则（P0）

**禁止**：直接使用中文 `user_query` 作为检索词。

优先级：
1. `expanded_query_variants` 非空 → 直接使用每条 `query` 字段（必须是英文）
2. `search_matrix` 非空 → 用每行 `must_include_terms` + `optional_terms` 拼接 Boolean query
3. `user_query` 本身是英文，且 `expanded_query_variants` 和 `search_matrix` 均为空/null → 允许 fallback，`fallback_used: true`，`risk_flags` 标注 `english_user_query_only_fallback`
4. 只有中文 `user_query` 且无英文 query variants → **返回空候选 + `fallback_used: true` + `risk_flags: ["missing_english_query_variants"]`，不执行检索**

## 默认 source 策略（P0）

默认启用：`openalex`、`semantic_scholar`、`arxiv`

P0.7a 自包含 Runtime 中：
- `openalex`、`arxiv`：正式纵向切片，必须可独立部署
- `semantic_scholar`：可选 best-effort metadata 源，失败时允许 partial success
- `deepxiv`、`agentic_data`：暂未启用；显式指定时写入失败诊断，不阻断健康源结果

## 核心任务

### 字段标准化边界

本 skill 只做字段名标准化映射（将各 API 字段统一为 `MetadataCandidate` schema），不做语义去重、相关性评分或质量筛选。同一论文被多个 API 返回时，保留所有条目并标记各自的 `source_name`，由下游 `candidate-dedup-and-provenance` 决定是否去重。

### 多源检索

对启用的 query × source 检索任务采用有界并行，默认最多 4 个请求同时执行。任一来源失败时记录到 `retrieval_report.failed_sources` 并继续保留健康来源结果；全部失败时返回空候选池并在 `retrieval_report` 中说明原因：

| source_name | 实现来源 | 特点 |
|---|---|---|
| `openalex` | `adapter.standalone_sources.search_openalex()` | 自包含 REST metadata 客户端 |
| `semantic_scholar` | `adapter.standalone_sources.search_semantic_scholar()` | 自包含可选 Graph API metadata 客户端 |
| `arxiv` | `adapter.standalone_sources.search_arxiv()` | 自包含 Atom metadata 客户端，无密钥 |
| `deepxiv` | P0.7a 未启用 | 显式请求时返回清晰诊断 |
| `agentic_data` | P0.7a 未启用 | 显式请求时返回清晰诊断 |

### 重复候选处理

**不做去重，也不按关键词、证据画像或固定分数提前筛选候选。** 同一篇文献可能被多个 query、多个 source 返回，全部保留，各自生成独立的 `candidate_id`。去重由确定性去重模块负责。

### 失败处理

- 单个 source 超时或报错：记录到 `retrieval_report.failed_sources`，继续其他 source
- 所有 source 失败：`retrieval_report.partial_success: true`，`metadata_candidates: []`，并在 `failed_sources` 记录原因
- `allow_partial_success: false` 时：任意 source 失败则整体失败
- metadata 字段缺失（如无 abstract）：保留候选，缺失字段填 `null`，记录到 `retrieval_warnings`

## Output

返回严格匹配 `output.schema.json` 的单个 JSON 对象。

`metadata_candidates[]` 每条包含：
- `candidate_id`：`{source_name}_{query_index}_{source_result_index}` 格式
- `dedupe_key`：优先 DOI，其次 arxiv_id，其次 openalex_id，其次 normalized_title hash；供下游去重使用
- `normalized_title`：小写、去标点的 title；供下游模糊去重使用
- `external_ids`：所有已知外部 ID（doi / arxiv_id / openalex_id / semantic_scholar_id / corpus_id / pmid / pmcid）
- 标准 metadata 字段：title、abstract、authors、year、venue、doi、arxiv_id、source_url、landing_page_url
- `citation_count`：数据源可提供时透出引用数；不可提供时为 `null`，供下游去重版本选择与评分使用
- `source_url`：来自 source API 的 PDF 或 landing URL，**仅作为 metadata**，不代表已下载、不代表可访问、不代表 OA 状态已判断
- `provenance[]`：每项包含 source_name / matched_query / search_intent / query_variant_type / query_index / source_result_index / source_rank / retrieved_at / retrieval_mode
- `raw_metadata`：原始 API 返回的完整字段
- `retrieval_warnings[]`：该候选的字段缺失或异常说明

`retrieval_report` 包含：
- `source_counts`、`query_counts`、`failed_sources`、`total_candidates`、`partial_success`、`fallback_used`、`risk_flags`

## pdf_url / source_url 边界说明（P0）

本 Skill 输出的 `source_url` 或 `raw_metadata` 中可能包含 PDF 链接，这些链接：
- **仅作为 metadata 字段保留**，不代表已下载、不代表链接可访问、不代表 OA 状态已判断
- OA / PDF / HTML / XML / TEI 可用性由后续 `source-access-readiness-resolver-skill` 处理

## Failure handling

- `user_query` 为中文且无英文 query variants：返回 `metadata_candidates: []`，`fallback_used: true`，`risk_flags: ["missing_english_query_variants"]`
- `user_query` 为空且无 query variants：返回 `metadata_candidates: []`，`risk_flags: ["no_query_input"]`
- 所有 source 均不可用：返回空候选池，`partial_success: true`，并记录 `failed_sources`
- DeepXiv / Agentic Data：P0.7a 自包含 Runtime 暂未启用，显式请求时记录到 `failed_sources`
- `allow_partial_success: false` 且任一源失败时，由 Runtime 返回失败诊断

## 不做事项（P0 边界）

- 不下载 PDF，不解析全文
- 不判断 OA / HTML / XML / TEI / PDF 可用性
- 不做候选去重，不做 title/abstract screening
- 不做候选评分，不做 Top-K selection
- 不做文献质量评价，不调用 LLM 判断论文相关性
- 不直接使用中文 user_query 作为检索词

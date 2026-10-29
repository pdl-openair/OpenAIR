# Source Mapping: academic-source-retrieval-skill P0.7a

## 当前部署切片

P0.7a 已将首个可部署的 metadata 检索 Runtime 收敛为 Skill 包内自包含实现，不再依赖旧项目目录 `04_backend`。

| source_name | Runtime 状态 | API 类型 | 密钥 | 说明 |
|---|---|---|---|---|
| `openalex` | 首批正式启用 | REST / JSON | 支持 `OPENALEX_API_KEY`；按服务端策略配置 | 返回 Works metadata；不下载 PDF |
| `arxiv` | 首批正式启用 | Atom XML | 无 | 返回 arXiv metadata；不下载 PDF |
| `semantic_scholar` | 可选 best-effort | Graph API / JSON | 可选 `SEMANTIC_SCHOLAR_API_KEY` / `S2_API_KEY` | 失败时允许 partial success |
| `deepxiv` | P0.7a 未启用 | — | — | 显式请求时返回失败诊断 |
| `agentic_data` | P0.7a 未启用 | — | — | 显式请求时返回失败诊断 |

## 自包含实现位置

```text
adapter/standalone_sources.py
adapter/query_adapter.py
adapter/metadata_mapper.py
adapter/retrieval_adapter.py
```

## 环境变量

| 变量 | 用途 | 默认值 |
|---|---|---|
| `OPENALEX_API_KEY` | OpenAlex API Key | 无 |
| `OPENALEX_MAILTO` | OpenAlex 联系邮箱 | 无 |
| `SEMANTIC_SCHOLAR_API_KEY` / `S2_API_KEY` | Semantic Scholar API Key | 无 |
| `OPENTRAJECTORY_RETRIEVAL_USER_AGENT` | Metadata 请求 User-Agent | `OpenAIR-Skill-Lab/0.7a ...` |
| `OPENTRAJECTORY_ARXIV_MIN_INTERVAL_SECONDS` | arXiv 最小请求间隔 | `3.0` |
| `OPENALEX_API_BASE_URL` / `ARXIV_API_BASE_URL` | 离线门禁注入本地 mock API | 官方 endpoint |

## 字段边界

- 仅返回 metadata 与来源 URL，不下载 PDF。
- OpenAlex abstract 从 `abstract_inverted_index` 重建。
- arXiv title、abstract、authors、year、category 在一次 Atom 响应中完成解析，不逐条二次请求。
- OA 可用性判断、PDF 抓取、全文解析、去重、筛选、评分和 Top-K 均由下游 Skill 负责。

# `retrieval-matrix-generation` Quality Contract

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Skill ID | `retrieval-matrix-generation` |
| Quality Contract Version | `v0.2.0` |
| 所属阶段 | Phase3 检索矩阵生成 |
| 上游 | Phase1 研究上下文、Phase2 约束与质量报告 |
| 下游 | `academic-source-retrieval-skill` |
| 更新时间 | `2026-06-02` |

## 2. 职责

将 Phase1 与 Phase2 结果转换为英文、可执行、可追溯的 query matrix。覆盖单对象、多对象、比较型、跨领域与边界型任务。

## 3. 非职责

- 不执行检索、下载、筛选或论文评价；
- 不修复上游语义缺失；
- 不将歧义、风险或常识包装成确认条件；
- 不为特定材料或 Scenario B 写硬编码；
- `assembled_facets` 只做策略归纳与可追溯传递，不做论文评分或质量语义决策。

## 4. 输出要求

| 输出 | 要求 |
| --- | --- |
| `search_matrix` | 当前 schema 要求 7 类 intent；各行职责不同，不得同义词换写 |
| `expanded_query_variants` | 英文、可执行、布尔正确、去重；比较任务覆盖 per-object 与必要 pairwise |
| `quality_report` | 使用当前已存在字段记录覆盖、约束使用、fallback 与 validation flags |
| `assembled_facets` | 基于上游和 matrix 可追溯归纳对象、机制、指标、上下文与排噪 facets |

## 5. 当前 schema 边界

当前 schema 没有独立 `abstain` / `reject` 字段，并强制输出固定数量的 matrix 与 variants。`quality_report.fallback_used` 和 `quality_report.validation_flags` 可记录部分风险，但不能替代 Eval 层质量结论。

因此：

- schema-valid 不等于效果通过；
- `fallback_used=true` 不得自动视为可接受；
- `reject_unreliable_input` 由后续 harness 在 Eval 层记录，不得假设生产字段已存在；
- 不得假设 `quality_report.upstream_quality` 等未落地字段存在。

## 6. 硬性失败

- query 为空、含 CJK / JSON 残留、布尔错误或明显重复；
- 风险、低置信候选值或推测进入 strict / must-have / NOT；
- 比较任务对象覆盖不平衡、出现全对象机械 hard AND，或完全缺失必要 pairwise；
- 明确 recency、language、source quality 或 out-of-scope 未结构化承载；
- 低价值 fallback 掩盖失败；
- 输出不满足当前 schema。

## 7. Eval 层降级规则

| `handling_class` | 允许行为 | Eval 结论 |
| --- | --- | --- |
| `normal_generate` | 正常生成高价值 query | 可进入候选通过 |
| `conservative_generate` | 生成保守入口并显式记录不足 | 必须人工复核，不得自动通过 |
| `manual_review_required` | 生成候选策略并暴露冲突或比较风险 | 必须人工裁决 |
| `reject_unreliable_input` | 不得用 fallback 冒充可靠结果 | Eval 层明确拒绝，不得进入 baseline 或通过率 |

## 8. 性能记录

首轮 baseline 只记录耗时分布，不设阈值。阈值仅在获得 baseline 后定义。

## 9. 人工复核触发

所有多对象、比较型、降级、边界和 regression 记录；任何 fallback、对象失衡、pairwise 缺失、来源策略不完整或规则与 Judge 冲突。

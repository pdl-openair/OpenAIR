# OpenTrajectory Skill 分类 Eval 指南

## 1. 原则

不同 Skill 类型不能机械使用同一套评分规则。

通用 Eval Suite 结构保持一致，但测试材料、确定性检查、Rubric、人工复核和真实链路验收必须按类别设计。


## 2. 类别验收强度总览

| 类别 | 人工复核 | 真实链路验收 | 说明 |
| --- | --- | --- | --- |
| Strategy | sampled | replay-required | 至少验证 Phase1 → Phase2 → Phase3 冻结 replay |
| Candidate Pipeline | sampled | replay-required；关键链路变更时升级为 mandatory-real-chain | 至少验证 query matrix → Top-K |
| Document Anchor | mandatory sampled review | mandatory-real-chain | 必须使用真实 PDF |
| Evidence | 前置节点 sampled；Evidence Extraction mandatory | Evidence Extraction mandatory-real-chain | 真实效果验收需足量文献 |
| Graph | mandatory | mandatory-real-chain | Graph 与 Discovery Package 分开验收 |
| Reasoning | mandatory | mandatory-real-chain | Lead、IDEA、Validation、Report 分别验收 |

具体 Skill 的强度应在其 `eval-spec.md` 中确认。

## 3. Strategy：结构解析与检索策略

### 适用范围

```text
structured-requirement-generation
constraint-interpretation-skill
retrieval-matrix-generation
```

### 主要风险

- 研究对象、指标或边界条件遗漏；
- 硬约束、偏好、噪声和待确认项混淆；
- query 不可执行；
- 多对象召回不平衡；
- 全对象 hard AND；
- 低价值 fallback；
- 单一领域术语过拟合；
- 性能过慢。

### 测试材料

- 核心科研问题；
- 跨领域问题；
- 模糊问题；
- 信息不足问题；
- 多对象比较型问题；
- 冲突约束；
- 历史失败样例。

### Deterministic Checks

- schema 与必填字段；
- 对象保留；
- query 非空；
- 禁止 fallback；
- 禁止全对象 hard AND；
- pairwise variants；
- 约束映射；
- 耗时。

### LLM Judge 重点

- 需求理解是否准确；
- 约束分类是否合理；
- query 是否有检索价值；
- 对象覆盖是否平衡；
- 是否跨领域通用。

### 人工复核

边界、跨领域和 regression 样例必须抽检。

### 真实链路验收

至少验证 Phase1 → Phase2 → Phase3 冻结 replay。

### 不要做什么

- 不要只检查 schema；
- 不要为单一锂电场景写不可泛化规则；
- 不要将 query 数量等同于检索价值。

## 4. Candidate Pipeline：来源召回与候选处理

### 适用范围

```text
academic-source-retrieval-skill
candidate-dedup-and-provenance
source-access-readiness-resolver-skill
title-abstract-screening-skill
candidate-source-scoring
candidate-tiering
topk-source-selection
```

### 主要风险

- recall 不足；
- 来源单一；
- 重复污染；
- provenance 丢失；
- readiness 错误；
- 误排除关键论文；
- 排序偏置；
- Top-K 缺乏代表性。

### 测试材料

- 真实 API 候选池快照；
- DOI、arXiv、标题变体和缺失 ID；
- structured fulltext、PDF、landing page、metadata-only；
- 边界摘要与噪声候选；
- 人工标注小样本。

### Deterministic Checks

- candidate ID；
- dedupe key；
- provenance；
- readiness 状态；
- 过滤前后数量；
- 分数范围；
- tier 合法性；
- Top-K 数量和来源分布。

### LLM Judge 重点

- 候选池相关性；
- 标题近似误合并；
- 误排除；
- 高分候选价值；
- Top-K 代表性。

### 人工复核

必须复核误排除率、Top-K 和边界样本。

### 真实链路验收

至少验证 query matrix → retrieval → dedupe → readiness → screening → scoring → tiering → Top-K。

### 不要做什么

- 不要只检查候选数量；
- 不要忽略误排除和来源偏置；
- 不要把单次 API 返回当作长期 baseline。

## 5. Document Anchor：文档解析与来源锚点

### 适用范围

```text
document-parse-anchor-skill
DocumentChunk
SourceAnchor
```

### 主要风险

- PDF 解析失败；
- chunk 边界失真；
- 章节定位错误；
- 表格、图注和正文混淆；
- SourceAnchor 不可回链；
- parser 降级未显式记录。

### 测试材料

- 真实 PDF corpus；
- metadata-only 样本；
- 解析失败样本；
- 章节、表格与图注样本；
- parser 降级样本。

### Deterministic Checks

- chunk / anchor 数量；
- ID；
- text hash；
- parser；
- status；
- chunk-anchor 绑定；
- 降级记录。

### LLM Judge 重点

- chunk 可读性；
- 章节定位；
- 表格与图注边界；
- 定位准确度。

### 人工复核

必须人工定位抽检。

### 真实链路验收

必须使用真实 PDF。

### 不要做什么

- 不要用 metadata fixture 替代 PDF 验收；
- 不要把 chunk 数量等同于解析质量；
- 不要忽略 parser 降级和 anchor 定位误差。

## 6. Evidence：Evidence 前置与抽取

### 适用范围

```text
sample-scan-for-schema
trace-schema-generation
data-level-evidence-extraction
```

### 主要风险

- 样本不具代表性；
- TRACE Schema 领域偏置；
- Evidence 无依据补全；
- 机制变量不具体；
- 边界或冲突证据丢失；
- Evidence 与 SourceAnchor 断链。

### 测试材料

- 固定 DocumentChunk / SourceAnchor 快照；
- 真实 PDF；
- 跨领域文献；
- 数值、机制、边界和冲突样本；
- 人工 Gold subset；
- Evidence 真实效果验收至少约 150 篇文献。

### Deterministic Checks

- sample 数量与内容类型覆盖；
- schema version；
- Evidence ID；
- EvidenceAnchorLink；
- SourceAnchor 回链；
- 批次与失败记录；
- 占位词；
- 无依据补全。

### LLM Judge 重点

- 样本代表性；
- TRACE Schema 可用性；
- Evidence 是否准确表达原文；
- 机制变量是否具体；
- 边界和冲突是否保留。

### 人工复核

Evidence Extraction 必须强制人工复核。

### 真实链路验收

必须验证 DocumentChunk / SourceAnchor → Evidence → EvidenceAnchorLink。

### 不要做什么

- 不要只做 schema check；
- 不要把 LLM 自身知识写成 Evidence；
- 不要用少量 PDF 代表 Evidence 真实效果验收。

## 7. Graph：Evidence Graph 与 Discovery Package

### 适用范围

```text
evidence-graph-construction
discovery-package-generation
```

### 主要风险

- 节点仍是占位概念；
- GraphEdge 无证据；
- 伪机制链路；
- 冲突证据被抹平；
- 断裂链路、缺失机制或弱证据位置遗漏。

### 测试材料

- 足量 Evidence；
- 冲突 Evidence；
- 弱证据；
- 完整链路与断裂链路；
- 跨领域 Graph。

### Deterministic Checks

- GraphNode；
- GraphEdge；
- Evidence ID；
- SourceAnchor；
- 占位词；
- 孤立节点；
- 重复节点；
- path ID；
- 冲突与弱证据标识。

### LLM Judge 重点

- 机制节点是否具体；
- 关系是否有证据；
- 路径是否可解释；
- 高置信路径、断裂、冲突和弱证据是否识别准确。

### 人工复核

必须强制人工复核。

### 真实链路验收

Graph 与 Discovery Package 分开验收。

### 不要做什么

- 不要只检查节点数和边数；
- 不要允许占位机制节点进入正式链路；
- 不要把 Graph 与 Discovery Package 合并验收。

## 8. Reasoning：Lead、IDEA、Validation 与 Report

### 适用范围

```text
lead-generation
idea-generation
reverse-validation
report-generation
```

### 主要风险

- 将假设写成证据；
- 将部分支撑写成完整支撑；
- 无依据推断未过滤；
- 缺失链路未展示；
- 适用边界丢失；
- 报告文案掩盖上游问题。

### 测试材料

- 完整支撑链路；
- 部分支撑链路；
- 无支撑负样本；
- 冲突与边界样本；
- Discovery Package、Graph 和 Evidence 快照。

### Deterministic Checks

- Evidence ID；
- 支撑等级；
- 缺失链路；
- 适用边界；
- 验证建议；
- 无支撑过滤或降权；
- 引用保留。

### LLM Judge 重点

- Lead 是否具备完整或较完整支撑链路；
- IDEA 是否清晰区分已有证据和待验证假设；
- Validation 是否准确输出完整支撑、部分支撑、无支撑；
- Report 是否克制、准确、可回溯。

### 人工复核

必须强制人工复核。

### 真实链路验收

必须分别验收 Lead、IDEA、Validation 和 Report，不得只检查最终报告。

### 不要做什么

- 不要把 IDEA 写成已有结论；
- 不要将部分支撑标记为完整支撑；
- 不要用最终报告文案掩盖上游证据问题。

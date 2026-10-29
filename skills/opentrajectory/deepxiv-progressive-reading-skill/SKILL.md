---
name: deepxiv-progressive-reading-skill
description: 对 source_screening_selection 已选来源按 EvidenceSchema 目标逐步深入阅读，输出保留真实来源身份、原文位置、source anchors 与未确认目标状态的 PaperPacket[]，供 evidence_extraction 直接使用。
---

# DeepXiv 渐进式文献深读

## 1. 目标与职责

本 Skill 属于 `progressive_reading`。它只做一件事：

> 对已选来源，根据当前 `EvidenceSchema` 中需要确认的对象、条件、结果和比较维度，优先读取最可能相关的原始 section；关键目标尚未定位时继续深入到预算允许的下一 section，并把真实读到的内容、原始位置和仍未确认的目标一起交给下游。

它不做来源重选、候选评分、Evidence / ResearchRecord 提取、机制综合、Findings、结论、假说或下一步建议。

## 2. 唯一正式输入

生产入口同时要求两路正式输入：

```text
topk_selection.selected_for_p03
+
evidence_schema
```

- `topk_selection.selected_for_p03`：来自 `source_screening_selection` 的已选来源；本 Skill 不创造 `candidates`、`selected_candidates` 等 alias。
- `evidence_schema`：来自最新 `evidence_schema_design` 的 ready `EvidenceSchema` 本体；它是必填输入，不是 hidden hint。
- `reading_config`：仅控制读取资源边界，不改变上游来源选择或 EvidenceSchema authority。

如果缺少任一路正式输入，生产入口直接拒绝；不提供 old/new 双入口或自动补字段。

## 3. EvidenceSchema 如何驱动渐进阅读

读取顺序只使用 EvidenceSchema 已公开表达的信息：

1. 优先处理 `required`，其次 `preferred`，最后 `optional` target。
2. 若 target 的 `description`、`condition_dimensions[]` 或 `comparison_requirements[]` 与 provider section 标题存在明确文本对应，优先读取该 section。
3. 否则根据 `target_type` / `scientific_role` 选择通用科学 section family，例如条件优先 Methods、结果指标优先 Results、机制优先 Mechanism/Discussion、比较要求优先 Comparison/Results。
4. EvidenceSchema 没有给出更具体方向时，才使用 `reading_config.section_priority` 作为通用后备排序。
5. 每读完一个 section，只检查 EvidenceSchema 中**明确给出的短语是否真实出现在已读原文**。所有当前 required / preferred target 已定位时可以提前停止；否则继续读取下一 section，直到 section 预算耗尽。

这里的 `located_in_read_content` 只表示 EvidenceSchema 明确短语在已读原文中可定位，并绑定到真实 source anchor；它**不等于**已经形成 ResearchRecord、Evidence 或科研结论。代码不生成同义词、不做科学语义补全，也不会把未读到的信息写成已确认。

## 4. 未命中与 degraded 语义

每个 `PaperPacket.diagnostics.target_reading[]` 都必须保留：

```text
target_id
target_type
scientific_role
requiredness
status = located_in_read_content | unresolved
matched_cues[]
missing_cues[]
source_anchor_ids[]
```

- 只有原文中实际出现的 EvidenceSchema 明确短语才进入 `matched_cues[]`。
- 没有定位到的短语留在 `missing_cues[]`，target 状态保持 `unresolved`。
- required target 在所有可用 packet 中仍 unresolved 时，整体结果至少为 `degraded`，但不会伪造 provider failure。
- provider 获取失败、错误论文/版本、材料类型错误等继续按真实 acquisition failure 报告，不能改写成“未发现证据”。
- `allow_raw_fallback=false` 仍是默认值。EvidenceSchema target 未命中本身不会自动打开 raw fallback。

## 5. Identity、材料与原文真实性

`requested_identity`、`provider_observed_identity`、`resolved_identity` 必须继续分离：

- requested 只表示上游希望读取的来源；
- observed 只能来自 provider 对当前文档自身身份的明确 metadata；
- resolved 只由 deterministic verification 产生；
- DOI / PMCID / OpenAlex 冲突直接拒绝；
- 请求具体 arXiv `vN` 时，provider 必须实际观察到同一版本；
- 正文、参考文献或 citation 中出现的 DOI/arXiv ID 不能反向认证当前文档。

材料类型在 acquisition 阶段保留。只有已验证 identity、稳定 locator、可用科学正文且类型为 `article_fulltext` 的材料可以成为 `MATERIAL_READY`。`ABSTRACT_ONLY` 明确是 degraded material，不伪装成已验证全文。

## 6. 输出与下游交接

主输出仍是既有 `paper_packets`，不改变其核心身份、section、anchor、provenance 结构。

每个可引用 section 继续保留：

```text
section_name
content
content_hash
provider
source_locator
paper_identity
material_type
material_status
```

每个 source anchor 继续由实际 section 内容和已验证 identity 稳定生成。EvidenceSchema 只影响“优先读什么、是否继续深入”，不会重写原文、重建来源或补造缺失内容。

`evidence_extraction` 可以直接使用 `paper_packets` 中的原文、anchors 和 `target_reading` 状态继续形成 ResearchRecord；本 Skill 不提前做提取。

## 7. 资源边界

`reading_config` 的正式边界继续执行：

- `max_papers`: 1..12；
- `max_sections_per_paper`: 1..5；
- `network_timeout_seconds`: 1..60；
- `max_provider_retries`: 0..2；
- `max_total_chars`: 10,000..2,000,000；
- `enable_non_arxiv_fulltext` 控制非 arXiv 全文路径；
- `allow_pdf_fulltext` 控制 PDF acquisition；
- `allow_raw_fallback` 默认 `false`，只有显式开启时才允许 raw fallback。

DeepXiv token 只从 `OT_DEEPXIV_TOKEN` 读取；Unpaywall email 只从 `UNPAYWALL_EMAIL` 读取。

## 8. 完成条件

一次执行必须同时做到：

1. 只读取上游正式选定来源；
2. EvidenceSchema 确实影响实际 section 读取顺序/深度；
3. 原文、identity、source locator、content hash 和 anchor 保持真实可追溯；
4. 未定位 target 明确保留 `unresolved`；
5. required target 未定位、abstract-only、部分读取失败或 provider failure 不伪装完整成功；
6. 不越权生成任何下游科研对象。

---
name: gap-analysis
description: 从已有综述、文献笔记、证据表或来源包中识别可追溯研究空缺，输出 G-ID、证据依据、空缺类型、置信度和补证据优先级；材料不足时必须停在缺输入状态，不编造创新点或未来方向。
---

# 研究空缺识别 (L3)

## Input

需要至少一种可复核上游材料：

- 综述草稿、文献笔记、来源表、证据表、claim-evidence 表或机制图。
- 候选论文摘要/全文摘录、图表说明、实验记录或用户整理的观点对照。
- 用户指定的空缺关注类型：理论、方法、数据、验证、应用、评价或复现。

只有 topic、关键词或一句“找创新点”时，不进入空缺识别；先要求补充来源或上游材料。

## Steps

1. 建立材料索引，给每条来源、证据、观点或机制节点分配可引用 ID。
2. 区分四类内容：
   - 直接由材料支持的已知结论。
   - 作者推断或讨论中的可能方向。
   - 材料内部冲突或证据不足的位置。
   - 用户关心但材料没有覆盖的问题。
3. 生成 G-ID 空缺条目，并给出空缺类型：
   - theoretical_gap
   - method_gap
   - data_gap
   - validation_gap
   - application_gap
   - reproducibility_gap
4. 为每条 G-ID 标注证据依据、缺什么证据、为什么重要、置信度和下一步补证据建议。
5. 对“可能是假空缺”的条目标记风险，例如检索范围太窄、只读综述、缺反例、领域术语不一致。

## Output

输出结构必须包含：

- `gap_list`：G-ID、空缺描述、空缺类型、关联材料 ID、证据强度、置信度。
- `not_a_gap`：看似空缺但材料已经覆盖或证据不足以判断的条目。
- `evidence_needed`：每条空缺下一步需要补的论文、实验、数据或分析。
- `priority`：高/中/低优先级和理由。
- `review_questions`：需要用户或导师确认的问题。

## Scenario Artifact Contract v2

Every proposed gap must survive traceability review. Include these sections:

1. `material_coverage`: source IDs, evidence IDs, covered subtopics, uncovered
   subtopics, coverage denominator, and coverage confidence.
2. `gap_list`: `G-ID`, gap statement, gap type, linked material IDs,
   linked evidence IDs, evidence strength, confidence, coverage status, gap
   criterion, opposing-evidence check, why it matters, and next evidence
   needed.
3. `not_a_gap`: items that look like gaps but are already covered, outside
   scope, caused by thin search coverage, or unsupported by the current
   material.
4. `needs_source` or `insufficient_coverage`: possible gaps that cannot be
   defended yet because source coverage is too thin. These are not publishable
   novelty claims.
5. `evidence_needed`: exact paper type, experiment, dataset, benchmark,
   negative result, or replication record needed to upgrade confidence.
6. `advisor_review_questions`: questions that change whether a G-ID is real.
7. `evidence_upgrade_queue`: ranked next sources or extraction tasks that would
   upgrade, narrow, or reject each candidate gap. For each item include the
   linked `G-ID`, source type, why it matters, expected locator, priority, and
   what finding would disconfirm the candidate gap.

Confidence labels must be `high`, `medium`, `low`, or `insufficient_materials`.
Do not use strong wording such as "field has not studied", "unexplored", or
"novel contribution" unless the supplied materials justify it.

Use the exact row key `G-ID` for every gap row; do not output variants such as
`g_id`, `gap_id`, or numeric-only identifiers. `confidence` must be one of the
string labels above, not a numeric probability.

When source coverage is partial, cap confidence at `medium` even if the local
evidence row is clear. For example, if the user says there are 12 papers but
only provides rows for 2 papers, the output may identify `candidate_gap`
patterns within the supplied material, but must not present them as high
confidence field-level gaps or novelty claims.

Use `candidate_gap` language when coverage is partial or messy. Use
`defensible_gap` only when the supplied source/evidence table is broad enough
for the stated scope and the opposing-evidence check has been performed. Always
distinguish "not found in the user's notes" from "absent from the field".

For messy early-stage notes, the most valuable output is not more speculative
gap wording. Prioritize a small, actionable upgrade plan: which 3-5 papers,
tables, methods sections, negative-result records, or advisor decisions should
be checked next; which G-ID each source would affect; and what evidence would
turn the item into `not_a_gap`.

## Fail Closed

- 不从 topic-only 生成研究空缺、创新点、论文卖点或假说。
- 不把“没有在当前材料里看到”写成“领域没人做过”。
- 每条空缺必须能追溯到上游材料 ID，无法追溯则标为 `needs_source`。

---
name: evidence-mining
description: 从论文全文、摘录、图注、表格、实验记录或结果日志中抽取 claim-evidence 条目，区分直接证据、间接证据、作者推断、冲突点、不确定性和缺失材料。适用于「证据抽取」「每条结论对应证据」「不要只复述摘要」「claim-evidence」「图注/结果段证据」等请求。没有可读材料、来源包或引用定位时不得输出逐条证据。
---

# 论文证据抽取 (L3)

## Goal
从用户提供的可读材料中系统性抽取结构化证据，建立 claim 到 evidence 的映射，并明确哪些内容是直接证据、间接证据、作者推断、冲突或缺失材料。

## Input
至少需要一种可读材料：

- 论文全文、结果段、方法段、图注、表格、补充材料片段。
- 用户摘录、实验记录、日志、指标表或来源包。
- 待核验 claim、证据目标或要比较的结论。

如果只有题目、题录、摘要或研究主题，停止并返回缺输入清单。

## Procedure

1. **材料边界检查**
   - 标出材料类型：全文/摘录/图注/实验记录/日志/题录/摘要。
   - 判断能否支持逐条证据抽取。
   - 如果材料不足，输出缺失材料，不补写证据。

2. **Claim 拆分**
   - 把用户关心的结论拆成最小 claim。
   - 每个 claim 只表达一个可检查判断。
   - 保留原文上下文，避免把作者讨论扩写成事实。

3. **Evidence 映射**
   - 为每个 claim 绑定证据条目。
   - 记录来源定位：论文/段落/图号/表号/实验记录 ID/日志片段。
   - 标注支持等级：direct / indirect / inference / conflict / missing。

4. **冲突与缺口**
   - 单独列出证据互相冲突、条件不可比、指标口径不同或缺少对照的地方。
   - 不把“可能解释”写成“已经证明”。

## Output
输出结构化证据表：

```markdown
| evidence_id | claim | support_tier | evidence_quote_or_observation | source_locator | uncertainty | conflict_or_gap |
|---|---|---|---|---|---|---|
```

并附：

- **材料可用性判断**
- **作者推断/模型解释清单**
- **冲突点清单**
- **缺失材料和下一步补证据建议**

## Scenario Artifact Contract v2

For messy or partial materials, the output must be directly reusable as a
research evidence table. Include these sections in order:

1. `material_readiness`: material type, usable range, unusable range, and why.
2. `claim_inventory`: `claim_id`, `claim_text` copied or tightly paraphrased
   from the material/user request, normalized minimal claim, source material
   ID, source locator, and whether the claim came from user wording, result
   text, caption, discussion, or model inference.
3. `claim_evidence_table`: `evidence_id`, `claim_id`, `support_tier`,
   `evidence_quote_or_observation`, `source_locator`, `condition_context`,
   `uncertainty`, `missing_context`, `conflict_or_gap`, and
   `next_verification`.
4. `unsupported_or_missing`: claims requested by the user that cannot be
   supported from the provided material. Include the unsupported claim,
   reason, and minimum material needed. Do not silently drop them.
5. `author_inference_or_model_inference`: statements that are interpretation,
   speculation, mechanism story, or discussion-only.
6. `handoff`: which rows can feed gap analysis, mechanism mapping, or result
   audit next.

Use support tiers exactly as:

- `direct`
- `indirect`
- `author_inference`
- `model_inference`
- `conflict`
- `missing`

If only an abstract, topic, title, bibliography entry, or search result is
provided, return `requires_existing_inputs` plus `missing_materials` and
`minimum_usable_input`. Do not create evidence rows.

For positive cases, cover every material-backed claim that affects the user's
request. A single row is not enough when the user supplied multiple result
paragraphs, figure captions, tables, or records. Separate direct observations
from mechanism/statistical/reliability interpretations.

## Constraints
- 必须基于用户提供材料或前序 substage 产出。
- 每条 evidence 必须可追溯到 source_locator。
- 摘要、题录、检索结果不能当作全文证据。
- 作者讨论、机制猜测、模型解释必须与直接证据分开。
- 材料不足时返回 requires_existing_inputs，不创建空证据表。

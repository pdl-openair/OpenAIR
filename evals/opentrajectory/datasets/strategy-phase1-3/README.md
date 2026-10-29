# Strategy Phase1–3 Shared Dataset

## 1. 用途

本目录保存 Phase1–3 共用的原始科研问题。三个 Skill 使用同一输入，但分别按自身职责验收：

```text
user_query
→ structured-requirement-generation
→ constraint-interpretation-skill
→ retrieval-matrix-generation
```

`expected` 是 Eval 标注，不是冻结输出 JSON，也不是生产 prompt。

## 2. 数据集边界

| 文件 | 数量 | 用途 |
| --- | ---: | --- |
| `core.jsonl` | 4 | 三类电池主场景 + 1 个 AI Agent 代表场景 |
| `cross-domain.jsonl` | 8 | AI Agent、RAG / Evidence Graph、理论材料、低维与功能材料 |
| `boundary.jsonl` | 12 | 模糊、信息不足、多对象比较、术语变体、冲突、来源范围、排噪与降级 |
| `regression.jsonl` | 1 | Scenario B 强约束占位；当前不可执行 |

## 3. Phase3 Eval 层处理分类

`expected.phase3.handling_class` 只属于数据集标注，不是当前 Skill 输出字段。

| 值 | 含义 | 是否允许自动通过 |
| --- | --- | --- |
| `normal_generate` | 应生成可用 query matrix | 可在硬门禁、评分与抽检均通过后进入候选通过 |
| `conservative_generate` | 可生成保守入口，但信息不足 | 否；必须人工复核，不得计为正常通过 |
| `manual_review_required` | 可生成候选策略，但存在冲突、比较平衡或语义风险 | 否；必须人工裁决 |
| `reject_unreliable_input` | 输入不足以支持可靠 query | 否；Eval 结论必须为拒绝，不得用 fallback 包装成功 |

边界区分：`conservative_generate` 是信息不完整但仍存在有价值的最小检索入口；`manual_review_required` 是存在冲突、比较平衡或语义风险，必须先暴露风险并由人工裁决。

## 4. 当前 schema 边界

当前 `retrieval-matrix-generation` schema 没有独立 `abstain` / `reject` 字段，并强制输出固定数量的 matrix 与 variants。后续 harness 必须在 Eval 层记录拒绝或人工复核结论，不能假设生产 schema 已支持该状态，也不能把 schema-valid 等同于效果通过。

当前可使用的生产字段仅包括既有 `quality_report.fallback_used` 与 `quality_report.validation_flags`。不得假设 `quality_report.upstream_quality` 等未落地字段存在。

## 5. 使用规则

- `core`、`cross-domain`、`boundary` 每行均为可执行科研问题。
- `regression.jsonl` 当前仅登记不可执行占位，不得进入运行、baseline、评分或通过率统计。
- 相对时间约束必须记录运行时解析基准日期。
- 不得为了提高当前版本分数删除困难样例。
- 本目录不包含 checks、harness、runs、reports 或 baselines。

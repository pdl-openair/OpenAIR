# `retrieval-matrix-generation` Eval Spec

## 1. 目标

验证 Phase3 是否生成真正可执行、可追溯且有召回价值的 query matrix。schema pass、固定数量满足或存在 fallback 都不能单独构成效果通过。

## 2. 版本与数据集

| 项目 | 内容 |
| --- | --- |
| Eval Suite | `strategy-phase1-3-v0.2.1` |
| Quality Contract | `v0.2.0` |
| Rubric | `v0.2.0` |
| 必跑数据集 | `core`、`cross-domain`、`boundary` |
| Regression | Scenario B 当前仅强约束占位，不可执行 |

## 3. Eval 层结论

后续 harness 必须独立记录 Eval 层结论，不得假设当前 Skill schema 已支持 `abstain` / `reject` 字段。

| `expected.phase3.handling_class` | Harness 最低处理要求 |
| --- | --- |
| `normal_generate` | 仅在硬门禁、评分和抽检均通过后进入候选通过 |
| `conservative_generate` | 强制人工复核；不得计为正常通过 |
| `manual_review_required` | 强制人工裁决；未完成复核前不得通过 |
| `reject_unreliable_input` | Eval 层明确拒绝；任何 schema-valid 输出都不得进入 baseline、评分或通过率 |

边界区分：保守生成应提供有价值的最小可用检索入口；低价值 fallback 是用无效退化掩盖失败，不能替代质量结果。

## 4. 硬门禁

| Gate ID | 失败条件 | 检查方式 |
| --- | --- | --- |
| P3-G01 | schema invalid | deterministic |
| P3-G02 | query 为空、含 CJK / JSON 残留、布尔错误或明显重复 | deterministic |
| P3-G03 | 比较任务对象覆盖不平衡、全对象机械 hard AND，或完全缺失必要 pairwise | deterministic + rubric + manual |
| P3-G04 | 明确 recency、language、source quality 或 out-of-scope 未结构化承载 | deterministic + manual |
| P3-G05 | 风险、未确认候选值或推测进入 strict / must-have / NOT | deterministic + rubric |
| P3-G06 | 低价值 fallback 掩盖失败，或 fallback 被当作正常通过依据 | rubric + manual |
| P3-G07 | `reject_unreliable_input` 样例被计入 baseline、评分或通过率 | deterministic |

对象失衡、必要 pairwise 缺失、query 不可执行和低价值 fallback 均是失败，不是仅扣分项。硬门禁失败时不得用总分覆盖失败结论。

## 5. Planned Deterministic Checks

> 本轮只定义规则，不实现代码。

| Check ID | 规则 | 适用范围 |
| --- | --- | --- |
| P3-C01 | 输出匹配 schema | all executable cases |
| P3-C02 | query 英文、布尔格式正确 | all executable cases |
| P3-C03 | 禁止明显重复 query 凑数 | all executable cases |
| P3-C04 | 每个比较对象至少有独立入口 | comparison / multi-object |
| P3-C05 | 比较任务包含必要 pairwise 组合 | comparison / multi-object |
| P3-C06 | 不得以全对象 hard AND 作为主入口 | comparison / multi-object |
| P3-C07 | recency、language、source quality、explicit exclusion 可追溯利用 | applicable cases |
| P3-C08 | `fallback_used=true` 时强制人工复核；低价值 fallback 直接失败 | fallback cases |
| P3-C09 | 按 `handling_class` 强制分流；降级与拒绝不得自动通过 | all executable cases |
| P3-C10 | Scenario B 占位不得执行、baseline、评分或生成假 fixture | regression placeholder |

## 6. Rubric-Based Grading

Rubric：`rubrics/rubric.md`

| 维度 | 权重 |
| --- | ---: |
| Query 可执行性与差异化价值 | 25% |
| 对象覆盖与比较完整性 | 30% |
| 约束策略化与可追溯性 | 20% |
| 忠实度与探索边界 | 15% |
| 降级诚实性 | 10% |

## 7. 通过条件

| 项目 | 通过条件 |
| --- | --- |
| 硬门禁 | 全部通过 |
| 加权分 | `>= 3.0 / 4.0` |
| 单项下限 | 每项 `>= 2`；前三项必须 `>= 3` |
| 人工复核 | 所有强制纳入样例完成裁决，无未解释高风险问题 |
| Regression | 仅运行具备真实 captured fixture 的 regression；Scenario B 当前不得运行 |
| 链路验收 | Phase1 → Phase2 → Phase3 → retrieval replay 无静默丢失 |

## 8. 首轮运行必须保存

Phase1 / Phase2 输入快照、Phase3 输出、模型、prompt 版本或 hash、runtime commit、耗时、checks、Judge 结果、人工裁决、trace 与 artifacts。

## 9. 当前阶段边界

不实现 checks，不运行 Skill，不修改 Skill、schema、sanitizer、registry 或 Git。

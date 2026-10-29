# `constraint-interpretation-skill` Eval Spec

## 1. 目标

验证 Phase2 能否把上游约束稳定转换为 Phase3 可消费的策略。重点不是“解释得像不像”，而是分层准确、可传递、可追溯、诚实降级。

## 2. 版本与数据集

| 项目 | 内容 |
| --- | --- |
| Eval Suite | `strategy-phase1-3-v0.2.1` |
| Quality Contract | `v0.2.0` |
| Rubric | `v0.2.0` |
| 必跑数据集 | `core`、`cross-domain`、`boundary` |
| Regression | Scenario B 仅属于 Phase3；Phase2 不得将占位当可执行 fixture |

## 3. 硬门禁

| Gate ID | 失败条件 | 检查方式 |
| --- | --- | --- |
| P2-G01 | schema invalid | deterministic |
| P2-G02 | 将歧义、低置信候选值或推测升级为 hard filter | deterministic + rubric + manual |
| P2-G03 | 明确时间、语言、来源质量或用户排除项未形成可传递策略 | deterministic + manual |
| P2-G04 | 冲突或关键缺口未进入风险、缺失项或人工复核状态 | deterministic + manual |
| P2-G05 | `downstream_search_terms` 含 CJK、解释句、完整问题或 JSON 残留 | deterministic |
| P2-G06 | 输出完整 Boolean query 或检索矩阵 | deterministic + manual |
| P2-G07 | 从 `risk_flags` 推导 NOT / 排除项 | rubric + manual |

硬门禁失败时不得用总分覆盖失败结论。

## 4. Planned Deterministic Checks

> 本轮只定义规则，不实现代码。

| Check ID | 规则 | 适用范围 |
| --- | --- | --- |
| P2-C01 | 输出匹配 schema | all |
| P2-C02 | `downstream_search_terms` 仅含可执行英文词组 | all |
| P2-C03 | `needs_user_confirmation=true` 的候选值不得进入硬过滤 | boundary |
| P2-C04 | 明确 recency、language、source quality、explicit exclusion 均可追溯传递 | boundary / cross-domain |
| P2-C05 | 冲突或关键缺口存在时，`quality_report.needs_manual_review=true`，并在缺失项或风险中可追溯 | boundary |
| P2-C06 | 约束级风险与 `quality_report.risk_flags` 不得相互矛盾；高风险项不得被汇总静默丢失 | all |
| P2-C07 | 不输出完整 Boolean query 或检索矩阵 | all |

## 5. Rubric-Based Grading

Rubric：`rubrics/rubric.md`

| 维度 | 权重 |
| --- | ---: |
| 约束分层准确性 | 30% |
| 可传递策略完整性 | 25% |
| 降级诚实性 | 20% |
| 风险标记一致性 | 15% |
| 跨领域泛化 | 10% |

## 6. 通过条件

| 项目 | 通过条件 |
| --- | --- |
| 硬门禁 | 全部通过 |
| 加权分 | `>= 3.0 / 4.0` |
| 单项下限 | 每项 `>= 2`；“约束分层准确性”“可传递策略完整性”必须 `>= 3` |
| 人工复核 | 强制纳入样例完成复核，无未解释高风险问题 |
| 链路验收 | Phase1 → Phase2 → Phase3 replay 无静默丢失 |

## 7. 首轮运行必须保存

Phase1 输入快照、Phase2 输出、模型、prompt 版本或 hash、runtime commit、耗时、checks、Judge 结果与人工复核结论。

## 8. 当前阶段边界

不实现 checks，不运行 Skill，不修改 Skill、schema、registry 或 Git。

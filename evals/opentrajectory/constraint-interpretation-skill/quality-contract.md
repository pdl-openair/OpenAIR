# `constraint-interpretation-skill` Quality Contract

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Skill ID | `constraint-interpretation-skill` |
| Quality Contract Version | `v0.2.0` |
| 所属阶段 | Phase2 约束解释 |
| 上游 | Phase1 输出、用户原始问题，以及 schema 允许的辅助上下文 |
| 下游 | `retrieval-matrix-generation` |
| 更新时间 | `2026-06-02` |

## 2. 职责

将明确约束和会影响检索的软约束解释为可传递策略。区分硬约束、软偏好、噪声排除、冲突与待确认项。

## 3. 输入优先级

`user_query` 是语义基线。`research_context`、`raw_constraints` 和其他 schema 允许字段只能辅助解释，不得覆盖用户原始语义。辅助上下文与原始问题冲突时，必须标记风险或人工复核，不得静默改写。

## 4. 非职责

- 不生成 Boolean query 或检索矩阵；
- 不执行检索、筛选或论文评价；
- 不将歧义、低置信候选值或风险标记升级为硬过滤；
- 不用领域推测生成阈值、材料体系或排除项。

## 5. 输出要求

| 输出 | 要求 |
| --- | --- |
| `interpreted_constraints[]` | 保留原始短语、解释值、候选值、置信度、确认状态、英文搜索词、候选过滤规则、依据和风险 |
| `quality_report` | 汇总覆盖度、缺失输入、风险与是否需要人工复核 |

## 6. 硬性失败

- 模糊项、低置信候选值或推测进入 hard filter；
- 从 `risk_flags` 推导 NOT / 排除项；
- 明确时间、语言、来源质量或用户排除项未形成可传递策略；
- 冲突或关键缺口被静默消解；
- `downstream_search_terms` 含 CJK、解释句、完整问题或 JSON 残留；
- 输出完整 Boolean query、检索矩阵或 schema invalid。

## 7. 允许降级

| 场景 | 允许行为 | 必须记录 | 禁止行为 |
| --- | --- | --- | --- |
| 定性条件 | 保留标准英文表达，候选值可为空 | `needs_user_confirmation`、风险 | 推断具体数值 |
| 冲突约束 | 同时保留冲突双方 | 风险与人工复核 | 静默选择一方 |
| 约束不足 | 可为空或仅保留明确对象边界 | `missing_inputs`、覆盖不足、人工复核 | 用完整问题伪造 condition |

## 8. 性能记录

首轮 baseline 只记录耗时分布，不设阈值。阈值仅在获得 baseline 后定义。

## 9. 人工复核触发

所有冲突、语言、时间、来源质量、排噪、模糊、信息不足，以及方法或机制类型存在歧义的样例。

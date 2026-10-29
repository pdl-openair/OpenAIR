# `structured-requirement-generation` Eval Spec

## 1. 目标

验证 Phase1 是否完成忠实结构化，而不是语义提炼或解释性扩展。共享问题集不代表共享评分标准。

## 2. 版本与数据集

| 项目 | 内容 |
| --- | --- |
| Eval Suite | `strategy-phase1-3-v0.2.1` |
| Quality Contract | `v0.2.0` |
| Rubric | `v0.2.0` |
| 必跑数据集 | `core`、`cross-domain`、`boundary` |
| Regression | 当前无 Phase1 专属可执行 regression |

## 3. 硬门禁

| Gate ID | 失败条件 | 检查方式 |
| --- | --- | --- |
| P1-G01 | schema invalid、缺字段或多字段 | deterministic |
| P1-G02 | 发明用户未确认信息，或静默丢失核心信息 | rubric + manual |
| P1-G03 | 输出 query、检索矩阵、方案或科研回答 | deterministic + manual |
| P1-G04 | `requirement_confidence < 0.6` 且 `ambiguities` 为空 | deterministic |

硬门禁失败时不得用总分覆盖失败结论。

## 4. Planned Deterministic Checks

> 本轮只定义规则，不实现代码。

| Check ID | 规则 | 适用范围 |
| --- | --- | --- |
| P1-C01 | 输出字段集合与 schema 一致 | all |
| P1-C02 | `original_user_query` 仅允许空白清理 | all |
| P1-C03 | 低置信输出必须显式记录缺口 | boundary |
| P1-C04 | 不得出现 Boolean query、检索矩阵或方案文本 | all |
| P1-C05 | 明确时间、语言与来源偏好进入 `requested_source_scope` | boundary / cross-domain |

## 5. 评分与通过条件

Rubric：`rubrics/rubric.md`

```text
最终结论 = 硬门禁 + 加权 Rubric Score + 人工裁决
```

| 项目 | 通过条件 |
| --- | --- |
| 硬门禁 | 全部通过 |
| 加权分 | `>= 3.0 / 4.0` |
| 单项下限 | 每项 `>= 2`；“需求忠实度”必须 `>= 3` |
| 人工复核 | 强制纳入样例完成复核，无未解释高风险问题 |

## 6. 首轮运行必须保存

输入快照、输出、模型、prompt 版本或 hash、runtime commit、耗时、checks、Judge 结果与人工复核结论。

## 7. 当前阶段边界

不实现 checks，不运行 Skill，不修改 Skill、schema、registry 或 Git。

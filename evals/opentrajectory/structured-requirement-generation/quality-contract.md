# `structured-requirement-generation` Quality Contract

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Skill ID | `structured-requirement-generation` |
| Quality Contract Version | `v0.2.0` |
| 所属阶段 | Phase1 结构解析 |
| 输入 | 用户原始科研问题 `user_query` |
| 下游 | `constraint-interpretation-skill` |
| 更新时间 | `2026-06-02` |

## 2. 职责

将用户问题转换为忠实、紧凑的 `StructuredRequirement`。保留明确对象、指标、边界、比较基线、输出意图、来源范围、缺口与置信度。

**忠实度优先于信息丰富度。** 不得为了让输出更完整而做解释性扩展。

## 3. 非职责

- 不生成 query、Boolean 组合或检索矩阵；
- 不展开机制、方法路线、候选材料或方案；
- 不回答科研问题；
- 不用领域常识补齐未确认信息。

## 4. 输出要求

| 字段 | 要求 |
| --- | --- |
| `original_user_query` | 仅清理空白，不改写核心含义 |
| `research_object` | 忠实保留核心对象或问题 |
| `target_metrics` | 仅保留用户明确指标或目标 |
| `boundary_conditions` | 仅保留明确条件、范围与场景 |
| `comparison_baseline` | 仅在用户明确提出时填写 |
| `output_intent` | 仅使用 schema 允许值；不确定时为 `null` |
| `requested_source_scope` | 仅保留明确时间、语言、来源类型或质量偏好 |
| `ambiguities` | 记录影响下游检索与判断的关键缺口 |
| `requirement_confidence` | 与输入完整度和 `ambiguities` 一致 |

## 5. 硬性失败

- 输出 query、检索矩阵、方案或科研结论；
- 发明材料、机制、阈值、协议、来源范围或基线；
- 静默丢失明确对象、指标、边界、比较关系或来源范围；
- `requirement_confidence < 0.6` 且 `ambiguities` 为空；
- 输出不满足当前 schema。

## 6. 允许降级

| 场景 | 允许行为 | 必须记录 | 禁止行为 |
| --- | --- | --- | --- |
| 问题模糊 | 保留最宽泛忠实对象 | 缺口、较低置信度 | 发明具体方向 |
| 信息不足 | 仅保留已知事实 | 缺失对象、指标、场景或来源范围 | 用常识补齐 |
| 比较关系不完整 | 保留已明确对象 | 未说明的基线或比较维度 | 伪造基线 |

## 7. 性能记录

首轮 baseline 只记录单样例耗时分布，不设性能阈值。阈值仅在获得 baseline 后定义。

## 8. 人工复核触发

所有 `boundary` 样例，以及低置信、模糊、多对象、来源范围复杂或规则与 Judge 冲突的样例。

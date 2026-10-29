# Field Semantics: constraint-interpretation-skill

本参考只补充字段边界；正式执行规则以 `SKILL.md` 为准。

## raw_constraints

标准字段：

```json
{"phrase": "电压窗口≤4.2V", "source": "user_query"}
```

兼容 `original_phrase`。同时存在时优先 `phrase`；输出统一写入 `original_phrase`。

## 约束状态如何映射到现有字段

当前 Schema 没有单独的 `constraint_status` 字段，因此使用现有字段表达，不创建第二套状态。

| 状态 | interpreted_value | candidate_values | needs_user_confirmation |
|---|---|---|---|
| 明确硬约束 | 明确值/语义 | 通常 `[]` | 通常 `false` |
| 明确软偏好 | 保留偏好对象/值 | 通常 `[]` | 语义清楚时可 `false` |
| 歧义 | 仅保留确定部分，否则 `null` | 仅保留用户/上游明确给出的候选 | 影响下游时 `true` |
| 明确不限 | `unrestricted` 或等价含义 | `[]` | `false` |
| 缺失/明确未知 | 不创建正向 constraint | 不猜值 | 视是否阻断决定 |

must-have、optional、exclusion 与 query 组合由 Retrieval Strategy Skill 根据这些正式语义字段决定。本 Skill 不生成自由文本过滤规则。

## candidate_values

只保留用户或正式上游已经明确给出的候选；等价单位表达如有检索价值，放入 `downstream_search_terms`，不要伪装成用户候选。

禁止：

- “快充”自动生成 `1C/2C/3C`；
- “低温”自动生成 `-20°C`；
- “层状氧化物”自动生成 `P2/O3`；
- “近期”自动生成年份范围；
- 非材料任务自动生成 benchmark、模型家族或 provider。

## confidence

confidence 是解释可靠性，不是约束硬度。

| 情况 | 建议区间 |
|---|---|
| 对象、语义角色、方向都明确 | `0.85–0.92` |
| 核心含义明确但存在轻微范围/字段不确定 | `0.70–0.84` |
| 定性含义可解释但边界不清 | `0.55–0.69` |
| 多个合理解释或信息不足 | `0.30–0.54` |

显式数字但方向未知，不得仅因“有数字”给高分。软偏好如果表达清楚，也可以高 confidence。

## constraint_type

优先使用：

- `numerical_range`
- `condition_threshold`
- `experimental_condition`
- `material_system`
- `material_property`
- `method`
- `mechanism`
- `source_preference`
- `boundary_condition`
- `required_condition`
- `qualitative_condition`

输出 Schema 当前允许字符串，因此无需为 `method` / `mechanism` 修改 Schema。

## downstream_search_terms

- 只用英文或英文/数字/单位组合；
- 只表达约束本体；
- 明确不限通常为空数组；
- 方向不明时保持中性，不补 `upper/lower/minimum/maximum`；
- 不把 candidate guess、解释句或 risk flag 放进检索词。

## risk_flags

只用 snake_case 短码，例如：

```text
explicit_value_from_user_input_only
constraint_direction_ambiguous
constraint_conflict_detected
charge_rate_unknown
material_system_unrestricted
```

不要写成解释句，也不要把 risk flag 当排除条件。

## quality_report

`coverage_level` 看关键约束是否正确处理，不看条数。明确“不限”和明确“未知”被正确识别，也属于覆盖完成的一部分。

`needs_manual_review` 只表示下游无法安全继续前必须确认。风险存在不等于自动阻断。

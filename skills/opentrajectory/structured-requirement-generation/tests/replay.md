# Replay

## Goal

验证 `structured-requirement-generation` 仅执行 `user_query -> StructuredRequirement`，输出字段严格受控，同时保留足够检索召回面。

## Global Checks

- 输出字段必须且仅包含：
  - `original_user_query`
  - `research_object`
  - `target_metrics`
  - `boundary_conditions`
  - `comparison_baseline`
  - `output_intent`
  - `requested_source_scope`
  - `ambiguities`
  - `requirement_confidence`
- `original_user_query` 与输入语义一致，允许必要空白清理，不允许改写核心含义。
- `output_intent` 只能是：`comparison` / `summary` / `decision_support` / `literature_review` / `hypothesis_generation` / `null`。
- `target_metrics`、`boundary_conditions`、`ambiguities` 每项应为短语或短句，不应写成长解释。
- `target_metrics`、`boundary_conditions`、`ambiguities` 各自不超过 8 项。
- `requirement_confidence` 在 `[0, 1]`。
- 不包含任意额外字段。
- 当 `requirement_confidence < 0.6` 时，`ambiguities` 必须非空。

---

## Case 1: 科研设计问题（假设生成 + 检索召回）

### Input

```json
{
  "user_query": "高电压快充条件下，如何设计锂金属电池电解液以提高循环稳定性并降低界面副反应？"
}
```

### Expected Checks

- `research_object` 应体现“高电压快充 + 锂金属电池 + 电解液设计”。
- `target_metrics` 应覆盖循环稳定性、界面副反应；不得把未提及指标（如首次库伦效率）写成已确认需求。
- `boundary_conditions` 应包含高电压快充、锂金属电池或锂金属负极、电解液设计等边界；不得默认补充温度范围、正极体系、测试协议。
- `output_intent` 应为 `hypothesis_generation`。
- `ambiguities` 应暴露电压范围、快充倍率、正极体系/电池形制、量化目标等关键缺口中的若干项。
- 不应直接给出电解液设计方案、机制解释或文献结论。

---

## Case 2: 信息完整的对比与选型研究

### Input

```json
{
  "user_query": "请比较 NMC811 和 LFP 电池在 -10°C 到 45°C 条件下的循环寿命与快充性能，给出面向电动两轮车场景的选型建议，优先参考近五年公开英文期刊与白皮书。"
}
```

### Expected Checks

- `research_object` 应体现 `NMC811` 与 `LFP` 的对比对象。
- `target_metrics` 至少包含“循环寿命”“快充性能”。
- `boundary_conditions` 应包含温度范围 `-10°C ~ 45°C` 与电动两轮车场景。
- `output_intent` 应为 `decision_support` 或 `comparison`。
- `requested_source_scope` 应体现“近五年 + 公开 + 英文期刊/白皮书”。
- `comparison_baseline` 不应为 `null`。
- `requirement_confidence` 预期 `>= 0.8`。

---

## Case 3: 用户问题模糊

### Input

```json
{
  "user_query": "我想做个材料方案，帮我找最好的路线，尽快能出结果。"
}
```

### Expected Checks

- `research_object` 可为当前可推断对象，但不得虚构具体材料体系。
- `target_metrics` 允许为空数组，或仅保留非常泛化指标。
- `boundary_conditions` 应为空数组或只包含用户明确给出的泛化约束。
- `comparison_baseline`、`requested_source_scope` 可为 `null`。
- `ambiguities` 必须指出关键缺口，例如材料体系、应用场景、评价指标、时间/资源边界。
- `requirement_confidence` 预期 `< 0.6`。

---

## Pass Criteria

- 所有用例均通过 Global Checks。
- 每个用例满足对应 Expected Checks，允许同义表达，不要求逐字匹配。
- 轻量运行时下，单次输出不应出现长段解释或方案生成内容。

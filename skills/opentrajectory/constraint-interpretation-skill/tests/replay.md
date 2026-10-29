# Replay Tests: constraint-interpretation-skill

## Global schema checks

- 输出严格匹配 `output.schema.json`；
- 每个 InterpretedConstraint 包含全部 12 个字段；
- `confidence` 在 `[0, 1]`；
- `downstream_search_terms[]` 不含 CJK；
- `risk_flags` 只用紧凑机器短码；
- 不得从领域常识补造阈值、材料体系、benchmark 或用户偏好。

## P0 contract checks

- `raw_constraints[].phrase` 是标准输入；`original_phrase` 仅兼容旧输入；
- 明确数值 + 单位 + 方向完整时，confidence 通常为 `0.85–0.92`；
- confidence 表示解释可靠性，不表示约束硬度；
- 模糊条件、软偏好、方向不明数值不得生成 hard filter；
- `candidate_values` 只保留用户/正式上游明确候选或完全等价表达，不用于领域猜值；
- 明确“不限”与明确“未知”必须区分：前者保留为 no-filter 边界，后者进入 `missing_inputs`；
- 冲突约束保留双方并标记，不替用户选择。

---

## Case 1：明确数值约束

**Input**
```json
{"user_query":"研究硅负极在3C快充下的循环性能，电压窗口0.01-1.5V，循环次数≥500次"}
```

**Expected**
- 电压窗口、循环次数语义明确，confidence 在高区间；
- 不得出现旧式统一 confidence；
- 数值方向完整时可给候选 hard filter；
- 检索词只含英文/数字/单位。

## Case 2：模糊定性约束

**Input**
```json
{"user_query":"高温下快充负极材料研究"}
```

**Expected**
- 至少一个约束保留歧义；
- 不得自行加入具体温度或 C-rate；
- `candidate_values` 不得出现领域猜测值；
- 只能给 soft / ambiguity rule；
- 是否 `needs_manual_review` 取决于该歧义是否阻断安全下游，不因存在 risk flag 自动为 true。

## Case 3：数值明确但方向未知

**Input**
```json
{"user_query":"电池测试里只记得 4.2 V，不能确认它是上限、下限还是截止电压。"}
```

**Expected**
- 保留 `4.2 V`，但不补 upper/lower/cutoff 方向；
- 不生成 hard filter；
- 方向会影响筛选时要求确认。

## Case 4：明确不限与明确未知

**Input**
```json
{"user_query":"倍率未知，不要假设；正极材料体系不限。"}
```

**Expected**
- 倍率进入 `missing_inputs`，不猜 C-rate；
- 材料体系以明确不限边界保留；
- 不生成任何具体正极材料检索偏好。

## Case 5：冲突约束

**Input**
```json
{"user_query":"测试温度不超过25°C，同时要求测试温度至少60°C；先保留冲突。"}
```

**Expected**
- 两条都保留；
- 标记 `constraint_conflict_detected`；
- 不静默选择、不平均；
- 冲突解决前不得把两条互斥 hard filter 同时用于下游。

## Case 6：legacy 字段兼容

**Input**
```json
{"user_query":"比较快充条件下循环稳定性","raw_constraints":[{"original_phrase":"温度范围25-45°C","source":"legacy"}]}
```

**Expected**
- 能读取 legacy 字段；
- 输出 `original_phrase: "温度范围25-45°C"`；
- 检索词不含中文。

完整近失配评测见 `tests/replay/wave1_03_near_mismatch_eval/`。

# Spec — Synthesis Assessment（可制备性评估）

<!-- Substage: synthesis_assessment
     Stage: solution_design
     L2 skill: synthesis-assessment
     L3 skills: crystal-synthesis-advisor, mp-search, research-lit -->

## 目标

判断候选是否有合理的制备路线、可能遇到哪些风险，以及已有模型、数据库和文献证据是否相互支持，决定候选是否进入参数推荐。

## Input

- `candidate`（候选编号 + CIF 路径，来自 candidate_generation / database_search）
- `stability_evidence`（已知的稳定性/结构弛豫证据，若无则标缺失）

## 产出物（output_file: `synthesis_assessment.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| candidate_refs | ✅ | 被评估候选的编号、化学式、CIF 路径 |
| precedent_evidence | ✅ | 目标相的数据库/文献先例（有/无 + 来源；无先例时明确写不确定性） |
| risk_assessment | ✅ | 竞争相与分相风险、元素挥发/毒性/易氧化风险（逐条标证据类型：数据库/文献/模型预测） |
| suitable_methods | ✅ | 适合该材料的制备方法列表（flux/CVT/固相/CVD 等）及理由 |
| feasibility_verdict | ✅ | 每个候选的结论：推荐进入参数推荐 / 有条件推荐（列条件）/ 不推荐（列原因） |
| equipment_constraints | ⭑ | 已确认的设备、气氛、温度、时长约束；未确认的设备不默认为可用 |

## 验收标准

- [ ] 每个风险点标注证据来源，模型预测与实验证据不混淆
- [ ] 未把「模型预测稳定」写成「实验一定能做出来」
- [ ] 没有文献先例的候选明确写出不确定性
- [ ] feasibility_verdict 对所有进入评估的候选都有明确结论
- [ ] 推荐结论衔接下游 `parameter_recommendation`（含所选制备方法）

## 与上下游的衔接

- **上游**：`candidate_generation` / `database_search`
- **下游**：`parameter_recommendation`（按所选制备方法推荐参数）、`stability_verification`（DFT 佐证）

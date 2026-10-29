# Spec — MP Constraint Search（多目标约束的 MP 种子检索）

<!-- Substage: database_search
     Stage: problem_modeling
     L2 skill: database-search
     L3 skills: materials-database-search -->

## 目标

从 Materials Project（MP）数据库中检索满足用户给定多目标性能约束的**已知真实晶体**，作为后续演化/合成的**种子候选**。

## 输入

- 约束集（bandgap / formation_energy / ehull / cl_score / f_max / exfoliation_energy 等的上下界）
- 元素范围 / 禁用元素列表
- 是否调用 crystal-property-prediction MCP 做交叉验证（可选）

## 产出物（output_file: `mp_seeds.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| constraint_set | ✅ | 用户约束集（结构化复述） |
| candidates[] | ✅ | 命中候选列表，含 mp_id / 化学式 / 空间群 / 各项性能值 |
| verified_by_cppred | ⭑ | 是否经 crystal-property-prediction MCP 交叉验证（含 9 项标量） |
| coverage_summary | ✅ | 命中数、约束命中率、不足项分析 |

## 验收标准

- [ ] constraint_set 与用户输入一致，无字段遗漏
- [ ] 每个候选都给出对应性能值（来自 MP 或 crystal-property-prediction MCP）
- [ ] candidates 至少含 mp_id + 化学式 + 空间群
- [ ] 若 candidates 为空，明确说明是哪一项约束命中率为 0
- [ ] coverage_summary 给出后续 candidate_generation 的引导建议（哪些子空间值得演化）

## 与上下游的衔接

- **上游**：意图理解（明确约束集 + 目标性能）
- **下游**：`candidate_generation`（若 MP 命中不足，进入演化生成补充候选）

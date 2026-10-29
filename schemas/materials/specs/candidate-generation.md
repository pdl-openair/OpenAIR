# Spec — Crystal Generation（DEMO 演化新晶体生成）

<!-- Substage: candidate_generation
     Stage: problem_modeling
     L2 skill: candidate-generation
     L3 skills: materials-new-candidate-generation -->

## 目标

通过 DEMO（Diffusion-based Evolutionary Multi-objective Optimization，基于 MatterGen）演化流水线，**生成满足多目标性能约束的新晶体候选**，输出 Pareto 前沿。

## 输入

- 约束集（bandgap / formation_energy / ehull / cl_score / f_max / exfoliation_energy 等的上下界）
- 目标函数（最大化/最小化哪些性能）
- MP 种子候选（可选，来自上游 `database_search`）
- 演化配置：种群规模、代数、干预策略

## 产出物（output_file: `novel_candidates.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| objectives | ✅ | 目标函数列表（如 max bandgap, min formation_energy） |
| constraints | ✅ | 约束集复述 |
| pareto_front[] | ✅ | Pareto 前沿候选，含 CIF 路径 / 各项性能 / 支配关系 |
| run_id | ✅ | 远端 mattergen MCP 的 run id |
| intervention_log | ⭑ | 中途干预记录（领域知识注入等） |
| novelty_check | ✅ | 与 MP 数据库对比的新颖性说明 |

## 验收标准

- [ ] objectives 与 constraints 与用户输入一致
- [ ] pareto_front 非空，每条候选含 CIF + 性能值
- [ ] 每条候选标注是否被支配、所属前沿层级
- [ ] intervention_log 记录每次干预的原因与效果
- [ ] novelty_check 给出与 MP 对比的命中/未命中情况

## 与上下游的衔接

- **上游**：`database_search`（种子候选）
- **下游**：`parameter_recommendation`（为 Pareto 候选推荐制备方案）

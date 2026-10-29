# cs_research_ope_001：OPE 方法在训练动态中的可靠性研究

## 场景

完整记录一项强化学习离线策略评估（OPE）实证研究的全流程：从文献调研、研究问题凝练、假说构建，到实验设计、任务拆解、代码实现和实验执行。

## 研究概述

- **核心问题**：在 RL 训练过程中，OPE 方法的可靠性如何随策略演化而变化？能否从可观测信号预测何时应该切换方法？
- **差异化**：填补 COBS(2021) "no single estimator dominates" 结论与 AutoOPE(2024) 黑盒自动选择之间的空白，提供可解释的决策规则。
- **目标会议**：NeurIPS 2026 Main Track (Empirical Study) 或 ICLR 2027。

## 阶段节点

| 阶段 | 状态 | 核心产物 |
|------|------|---------|
| problem_modeling | completed | 文献调研综述、研究提案 v3（3 个递进式假说链） |
| solution_design | completed | 实验设计方案 v2（4 环境 × 7 OPE 方法）、任务拆解计划（54 任务 / 14 Set） |
| execution_validation | running (约 75%) | sb3_ope 完整代码库（43 测试通过）、Set 1-9 已完成、Set 10-13 进行中 |
| insight_generation | not_started | — |

## 能力调用清单

### problem_modeling 阶段（3 个调用）

| 调用 | 说明 |
|------|------|
| literature_review | OPE 领域文献检索与综述 |
| question_formulation | 从研究空白凝练核心研究问题 |
| hypothesis_construction | 构建 3 个递进式假说链（v1→v2→v3 三轮迭代） |

### solution_design 阶段（2 个调用）

| 调用 | 说明 |
|------|------|
| experiment_design | 4 环境 × 7 OPE 方法系统性基准实验设计 |
| task_planning | 54 任务 / 14 Task Set 拆解与依赖分析 |

### execution_validation 阶段（5 个调用，2 个运行中）

| 调用 | 说明 |
|------|------|
| ope_meta_execute | 初始化执行阶段，管理跨 session 状态 |
| code_implementation | sb3_ope 全部模块实现（数据层 + 8 种估计器 + 评估框架） |
| ope_session_execute | 执行 Set 1-9（已完成） |
| experiment_run_monitor | Set 10-13 主实验执行与监控（运行中） |
| experiment_taskpool | 任务池调度与进度管理（运行中） |

## 产物文件

| 文件 | 阶段 | 来源 |
|------|------|------|
| literature.md | problem_modeling | proposal/literature.md |
| proposal-v3.md | problem_modeling | proposal/proposal-v3.md |
| experiment_design-v2.md | solution_design | design/experiment_design-v2.md |
| task_plan.md | solution_design | plan/task_plan.md |

## 数据来源

- 运行记录原始目录：`S:\research\OpenAIR\openair\ope-benchmark\`
- 代码库路径：`S:\research\OpenAIR\sb3_ope\`
- 状态元数据：`S:\research\OpenAIR\openair\.current_research`

## 用途

- 前端任务详情页展示完整科研流程
- 任务看板展示多阶段任务流转
- 主工作台展示执行中的能力调用
- Skill 回放测试参考样例

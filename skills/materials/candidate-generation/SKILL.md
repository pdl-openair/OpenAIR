---
name: candidate-generation
description: 把性能目标、约束与数据库种子提交给生成式演化流水线（扩散变异 + 多目标进化搜索），跟踪演化过程并在必要时干预，输出 Pareto 前沿的新候选晶体。
---

# Candidate Generation（L2 编排）

## 职责

把"性能目标 + 约束 + 可选种子"提交给生成式演化流水线（远端 MCP），跟踪运行、必要时干预，最终输出 Pareto 前沿候选至 `novel_candidates.md`。

本 L2 是**域无关**编排：演化后端（mattergen / 其他）、候选表征（晶体 / 分子 / 配方）由 L3 实现。L2 只负责：参数对齐、运行轮询、领域知识注入、Pareto 前沿格式化。

## 执行步骤

1. 读取上游 `database-search` 产出的种子清单（如有）作为演化种子
2. 与用户确认 objectives（最大化/最小化目标）与 constraints
3. 调用域特定 L3（materials pack 调用 `materials-new-crystal-generation`）提交演化 run
4. 轮询进度；物理/化学上合理时执行领域知识注入/干预
5. 拉回 Pareto 前沿，做新颖性比对
6. 按 spec（`schemas/materials/specs/candidate-generation.md`）产出 `novel_candidates.md`

## 产出

见 spec。output_file = `novel_candidates.md`。

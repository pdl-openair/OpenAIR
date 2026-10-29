---
name: synthesis-assessment
description: 评估候选材料的可制备性：制备路线先例、竞争相与分相风险、元素挥发与毒性、适合的制备方法与设备可行性，产出可制备性评估报告。
---

# Synthesis Assessment（L2 编排）

## 职责

判断候选是否有合理的制备路线、可能遇到哪些风险，以及已有模型、数据库和文献证据是否相互支持。按规范产出可制备性评估报告（output_file: `synthesis_assessment.md`），决定候选是否进入参数推荐。

对应材料发现四阶段流程的 2.6「评估候选是否适合制备」。

## 执行步骤

1. 收集上游候选（`candidate_generation` / `database_search` 的产物）及已有的结构、稳定性、快速性质证据
2. 调用 `crystal-synthesis-advisor` L3 评估：
   - 目标相是否已有数据库或文献先例
   - 竞争相和分相风险
   - 元素是否挥发、有毒或容易氧化
   - 结构弛豫和稳定性是否支持该候选
   - 哪些制备方法适合该材料
   - 设备、气氛、温度和时长是否可满足
3. （可选）调用 `mp-search` / `research-lit` L3 补充数据库与文献证据
4. （可选）调用 `pymatgen` L3 做结构解析辅助
5. 按 spec（`schemas/materials/specs/synthesis-assessment.md`）产出评估报告

## 编排说明

- L3 选择：核心评估用 `crystal-synthesis-advisor`；数据库先例查 `mp-search`；工艺文献查 `research-lit`
- 传给 L3 的上下文：候选 CIF 路径与候选编号、已知的稳定性/性质证据、实验室设备约束（若有）

## 产出

见 spec。output_file = `synthesis_assessment.md`（materials pack 约定）。

## 验证要点

- 「模型预测稳定」没有被写成「实验一定能做出来」；没有文献先例时不确定性已明确写出
- 每个风险点都有证据来源（数据库/文献/模型预测，标注类型）
- 明确给出是否推荐进入参数推荐的结论

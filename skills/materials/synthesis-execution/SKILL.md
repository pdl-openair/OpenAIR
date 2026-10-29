---
name: synthesis-execution
description: 把候选材料、制备评估与推荐配方整理成实验人员可执行的指派单，回收实际制备记录与表征结果，对比预测、DFT 与实测三方数据。
---

# Synthesis Execution（L2 编排）

## 职责

把候选 CIF、制备评估和推荐配方整理成实验人员可以执行的指派单，明确目标、原料、工艺、风险和需要回收的表征；再由实验人员完成实际操作，回收真实样品和表征数据，对比机器学习预测、DFT 计算和实验测量。按规范产出执行记录（output_file: `synthesis_execution_log.md`）。

对应材料发现四阶段流程的 3.6「准备湿实验指派」、3.7「实际制备、样品台账和表征回收」、3.8「比较预测值、DFT 值和实测值」。

## 执行步骤

1. 收集上游输入：候选编号与 CIF（`candidate_generation`）、可制备性评估（`synthesis_assessment`）、推荐配方（`parameter_recommendation`）
2. 调用 `wet-lab-workflow` L3 生成湿实验指派单（`wetlab/assignments/<candidate_id>_assignment.md`），包含：候选编号和 CIF、目标性质、主推方案、备选方案、温度和气氛、风险安全事项、预期表征项目、数据回收要求
3. 实验人员执行后，调用 `wet-lab-workflow` L3 维护样品台账（`wetlab/sample_ledger.csv`）与表征结果（`wetlab/characterization_results.csv`）
4. 调用 `wet-lab-workflow` L3 生成预测/DFT/实测三方对比（`wetlab/comparison/comparison_<date>.md`）；需要纳入最终报告时衔接 `report_generation`
5. 按 spec（`schemas/materials/specs/synthesis-execution.md`）产出执行记录

## 编排说明

- L3 选择：指派单/台账/表征/对比统一用 `wet-lab-workflow`；需要补充候选性质时可调用 `pymatgen`
- 推荐参数是「计划参数」，不能提前写成「实际执行参数」
- 没有实际测量的数值必须留空或标为待完成，不能从 ML 或 DFT 结果复制成实测值
- 传给 L3 的上下文：候选编号、CIF 路径、制备评估与配方文件路径、实验室设备约束

## 产出

见 spec。output_file = `synthesis_execution_log.md`（materials pack 约定）。指派单、台账、表征与对比文件在 `wetlab/` 下。

## 验证要点

- 指派单要素齐全（候选/目标/方案/温度气氛/风险/表征/回收要求）
- 样品编号与候选编号对应无误后才能做三方对比
- 对比前先核对性质定义和单位；凸包能与相纯度不是同一个物理量，只做有边界的定性对照
- 未回收的表征标为待完成，不阻塞其他阶段推进

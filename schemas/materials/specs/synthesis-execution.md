# Spec — Synthesis Execution（合成执行与湿实验）

<!-- Substage: synthesis_execution
     Stage: execution_validation
     L2 skill: synthesis-execution
     L3 skills: wet-lab-workflow -->

## 目标

把候选 CIF、制备评估和推荐配方整理成实验人员可执行的指派单，回收实际制备记录与表征结果，并对比机器学习预测、DFT 计算和实验测量三方数据。

## Input

- `candidate`（候选编号 + CIF，来自 candidate_generation）
- `synthesis_assessment`（可制备性评估报告）
- `parameter_recommendation`（推荐配方，含主推与备选方案）

## 产出物（output_file: `synthesis_execution_log.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| assignments | ✅ | 湿实验指派单清单（`wetlab/assignments/<candidate_id>_assignment.md`：候选编号和 CIF、目标性质、主推/备选方案、温度和气氛、风险安全事项、预期表征项目、数据回收要求） |
| sample_ledger | ✅ | 样品台账（`wetlab/sample_ledger.csv`：样品、日期、方法、原料、温度、气氛、结果状态） |
| characterization_results | ⭑ | 表征回收（`wetlab/characterization_results.csv`：样品、测试技术、性质、数值、单位、仪器、原始数据路径）；未回收时标待完成 |
| comparison | ⭑ | 三方对比（`wetlab/comparison/comparison_<date>.md`：ML 预测 vs DFT vs 实测）；仅在表征回收后生成 |

## 验收标准

- [ ] 指派单要素齐全，推荐参数标注为「计划参数」而非「实际执行参数」
- [ ] 没有实际测量的数值留空或标待完成，未从 ML 或 DFT 结果复制成实测值
- [ ] 样品编号与候选编号对应核对无误后才做三方对比
- [ ] 对比前核对性质定义和单位；带隙比较注明计算方法和实验测量方法；晶格参数按结构和精修方法解释；凸包能与相纯度只做有边界的定性对照
- [ ] 未回收表征不阻塞其他阶段（DFT/报告可先行，标待实验）

## 与上下游的衔接

- **上游**：`synthesis_assessment`、`parameter_recommendation`
- **下游**：`report_generation`（湿实验结果与三方对比纳入最终报告）

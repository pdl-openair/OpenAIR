---
name: materials-report-generation
description: 汇总材料发现全流程的真实证据（数据库检索、候选演化、计算验证），生成结构完整、数字可溯源、缺失步骤明确标注的分析报告与图表。
---

# Report Generation（L2 编排）

## 职责

汇总前面已经取得的真实证据，生成结构完整、数字有来源、缺失步骤明确的 Markdown 实验报告。按规范产出报告（output_file: `final_report.md`）及配套图表。

对应材料发现四阶段流程的「阶段四：报告生成」（4.1–4.5）。

## 执行步骤

1. 调用 `experiment-report` L3 收集全流程证据（内部脚本 `collect_evidence.py` 扫描研究目录，产出 `report/evidence_manifest.json`）：确认目标、数据库、候选、ML、DFT、湿实验和图表文件哪些存在、哪些为空、哪些缺失
2. 调用 `figure-spec` L3 生成方法流程图、阶段关系图（如 `report/figures/pipeline.svg`）
3. 调用 `paper-figure` L3 用真实结果数据绘制候选性质图、Pareto 图、预测与 DFT/实验对比图
4. 调用 `experiment-report` L3 按固定章节撰写 Markdown 报告：
   1. 研究目标与限制；2. 方法总览；3. 数据库调研和初始材料；4. 结构生成、搜索和候选筛选；5. DFT 带隙和热力学稳定性；6. 湿实验执行和样品记录；7. 机器学习、DFT 与实验三方对比；8. 结论与下一步建议；9. 缺失环节和不确定性；10. 证据、目录和复现信息附录
5. 核对数字来源和结论范围（见验证要点）
6. 后续湿实验或延迟 HPC 结果回来时，只更新受影响的证据、图表和章节，不删除原有计算记录，注明新增数据日期和对应候选

## 编排说明

- L3 选择：证据收集与报告主体 `experiment-report`；流程/架构图 `figure-spec`；数据图 `paper-figure`
- 报告 Skill 只汇总已有结果，不为了写报告自动重跑模型或 DFT
- 传给 L3 的上下文：任务工作区路径、研究报告输出目录（`report/`）、已完成各阶段的产物位置

## 产出

见 spec。output_file = `final_report.md`（materials pack 约定）。图表在 `report/figures/` 下，证据清单 `report/evidence_manifest.json`。

## 验证要点

- 每个数字都有来源文件和必要的单位；候选编号、样品编号和结果编号能够对应
- 降级路径、失败任务和跳过步骤已写明
- 没有湿实验结果时不使用「实验验证成功」；只有静态带隙时不写成完整能带计算；机器学习初筛通过不写成 DFT 已确认；DFT 通过也不写成实际制备已经成功
- 图表所有数值来自真实结果；没有足够数据时省略该图或写明缺失，不生成虚构趋势

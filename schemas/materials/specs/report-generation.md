# Spec — Report Generation（报告生成）

<!-- Substage: report_generation
     Stage: insight_generation
     L2 skill: materials-report-generation
     L3 skills: experiment-report, figure-spec, paper-figure -->

## 目标

汇总材料发现全流程已取得的真实证据，生成结构完整、数字有来源、缺失步骤明确的 Markdown 实验报告，附证据清单与可复现图表。

## Input

- 任务工作区中前三阶段的真实文件（目标说明、MP 报告、种子/候选 CIF、ML 预测、DFT 结果、湿实验记录、图表）

## 产出物（output_file: `final_report.md`）

| 字段 | 必填 | 说明 |
|---|---|---|
| evidence_manifest | ✅ | 全流程证据清单（`report/evidence_manifest.json`：存在/为空/缺失状态） |
| report_sections | ✅ | 固定十章：研究目标与限制 / 方法总览 / 数据库调研和初始材料 / 结构生成搜索和候选筛选 / DFT 带隙和热力学稳定性 / 湿实验执行和样品记录 / ML-DFT-实验三方对比 / 结论与下一步建议 / 缺失环节和不确定性 / 证据目录和复现信息附录 |
| figures | ⭑ | 流程图（figure-spec）与数据图（paper-figure），所有数值来自真实结果 |
| update_log | ⭑ | 后续补充数据时的更新记录（新增数据、日期、对应候选；不删除原有计算记录） |

## 验收标准

- [ ] 每个数字都有来源文件和必要的单位；候选编号、样品编号和结果编号能够对应
- [ ] 降级路径、失败任务和跳过步骤已写明
- [ ] 证据类型不混淆：没有湿实验结果不用「实验验证成功」；只有静态带隙不写成完整能带计算；ML 初筛通过不写成 DFT 已确认；DFT 通过不写成制备成功
- [ ] 图表数值全部来自真实结果；数据不足时省略该图或写明缺失，不生成虚构趋势
- [ ] 报告不为了成稿自动重跑模型或 DFT

## 与上下游的衔接

- **上游**：全部前序 substage（`literature_review` → `synthesis_execution`）
- **下游**：无（终点 substage；后续湿实验/HPC 结果回来后按 update_log 增量更新）

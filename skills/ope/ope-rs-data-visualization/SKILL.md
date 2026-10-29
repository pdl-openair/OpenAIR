---
name: ope-rs-data-visualization
description: 当用户想要清洗 OPE 实验数据、制作可视化图表、准备论文图表时自动触发。常见场景：用户说"整理 OPE 数据"、"画 MSE 对比图"、"制作消融图表"、"做显著性检验"。本 skill 完成数据聚合、统计检验、图表绘制与标注，不负责论文撰写。
---

# 数据可视化（L3 — OPE）

清洗 OPE 实验数据，制作可视化图表，为论文写作准备数据支撑。

## Method

### 子任务 1：数据清洗与统计

1. **数据加载与探索**
   - 读取 `execute/data/processed/merged.csv`
   - 数据基本统计（均值、标准差、分位数）
   - 分布检查（正态性检验）

2. **数据聚合**
   - 按 algorithm × env 聚合
   - 计算跨种子的均值与 95% 置信区间
   - 生成对比表

3. **统计检验**
   - t 检验 / Mann-Whitney U 检验（两两对比）
   - ANOVA / Kruskal-Wallis 检验（多组对比）
   - 多重比较校正（Bonferroni / Holm）

### 子任务 2：图表绘制

1. **图表类型选择**
   - 性能对比（MSE/Bias/Variance）：柱状图、箱线图
   - 样本效率曲线：折线图（带置信区间）
   - 消融分析：分组柱状图
   - 算法 × 环境矩阵：热力图

2. **图表规范**
   - 分辨率：300 DPI（论文）/ 150 DPI（slides）
   - 字体：Times New Roman / Arial
   - 尺寸：单栏 3.5in / 双栏 7in
   - 颜色：Colorblind Friendly / 灰度友好

3. **绘图工具**
   - Python：matplotlib / seaborn / plotly
   - R：ggplot2
   - 或用 Kaleido 导出静态图

### 子任务 3：图表标注与说明

1. **图表标注**：标题、轴标签（含单位）、图例、关键发现注释
2. **Caption 撰写**：
   ```
   Figure 1: OPE estimator comparison on D4RL.
   (a) MSE on Hopper. (b) Aggregated across all envs. Lower is better.
   ```
3. **图表目录**：编号体系、交叉引用列表、文件管理

## Input

- 清洗后数据（来自 `ope-rs-data-validation` 的 `execute/data/processed/merged.csv`）
- 可选：图表类型偏好、目标期刊/会议格式

## Output

按 `schemas/ope/specs/visualization.md` 模板产出，使用 Write 工具写入 `report/visualization.md`，结构：
- 数据概览（统计表 + 检验结果）
- 图表清单（类型 + 描述 + 文件路径）
- 图表文件目录树
- 图表 Caption（每张图的说明）
- 下一步行动

## Constraints

- 数据驱动：图表准确反映数据，不误导（如坐标轴不截断制造差异假象）
- 可重复：统计检验结果必须可复现
- 规范统一：所有图表风格保持一致
- 图表规范符合目标期刊/会议要求
- 多轮讨论：用 AskUserQuestion 确认图表设计
- 结果落盘到 `report/visualization.md` + 图表文件到 `report/figures/`

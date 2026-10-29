---
name: analysis-report-durable
description: >
  新材料发现分析报告生成。LLM agent 读取所有前序产物，
  自主规划图表，内联调用 figure_renderer.py 生成流程 SVG，用 matplotlib
  生成数据分析图，产出 analysis_report.md（结论先行、证据支撑、用户友好）。
  替换 experiment_report（llm_reasoning）。
---

# analysis-report-durable

durable_agent skill：LLM 在单节点边界内自主整合全链路产物，产出图文并茂的新材料发现分析报告。

## 工作流

1. 读取工作区所有前序产物（candidates.json、top_k_table、S7 DFT 结果）
2. 自主规划图表清单（至少：链路流程图、Pareto/候选属性散点图或柱状图）
3. 调 figure_renderer.py 生成链路流程 SVG
4. 调 matplotlib 生成数据分析 PNG
5. 整合所有证据 + 图表路径，撰写 analysis_report.md

## 报告结构（结论先行）

```
# 新材料发现分析报告

## 核心结论
（一句话：找到了什么材料、稳定性如何、建议）

## 发现的候选材料
## 材料发现方法
## 数据库检索结果
## 进化设计结果
## 热力学稳定性验证
## 图表
## 局限性与下一步
```

## 图表类型

| 图表 | 类型 | 工具 |
|---|---|---|
| 材料发现链路流程图 | SVG | figure_renderer.py |
| Pareto 前沿散点图（带隙 vs ehull） | PNG | matplotlib scatter |
| 候选材料性质柱状图 | PNG | matplotlib bar |

## 缺失数据处理

每个缺失环节显式标注 `⚠️ 数据缺失：<原因>`，报告结构保持完整。

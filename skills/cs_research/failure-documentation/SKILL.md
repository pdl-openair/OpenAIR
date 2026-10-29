---
name: failure-documentation
description: 系统化记录和分析失败实验
---

# 失败记录 (L3)

## Goal
系统化记录和分析失败实验（negative results），提取失败模式，防止重复犯错，为后续研究方向调整提供依据。

## Input
实验日志、错误报告和假设判定中的不支持结论。

## Output
失败实验记录，每条包含：实验 ID、失败描述、根因分析（代码 bug / 方法不适用 / 超参不当 / 数据问题）、修复尝试记录、经验教训总结、是否值得重试评估。

## Constraints
- 必须基于前序 substage 的 spec 产出
- 产出必须可追溯

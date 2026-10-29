---
name: ablation-design
description: 规划逐步移除/替换组件的实验
---

# 消融设计 (L2)

## Goal
规划逐步移除/替换组件的实验

## Must Do
1. 规划逐步移除/替换关键组件的消融实验
2. 确保消融矩阵覆盖所有假设相关维度
3. 输出 ablation-plan spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/02-method/ablation-plan.md` 模板产出，使用 Write 工具写入当前任务工作区的 `02-method/ablation-plan.md`。

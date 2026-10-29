---
name: task-breakdown
description: 拆为可执行步骤+实验矩阵
---

# 任务拆解 (L2)

## Goal
拆为可执行步骤+实验矩阵

## Must Do
1. 将整体研究计划拆解为可执行的原子任务
2. 构建实验矩阵（方法×数据×种子×消融）
3. 输出 task spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/02-method/task.md` 模板产出，使用 Write 工具写入当前任务工作区的 `02-method/task.md`。

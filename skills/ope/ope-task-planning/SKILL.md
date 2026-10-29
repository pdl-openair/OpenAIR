---
name: ope-task-planning
description: 将实验方案分解为可执行任务计划
---

# 任务规划 (L2 — OPE)

## Goal
将实验设计方案分解为有序的可执行任务，建立依赖关系与时间线。

## Must Do
1. 将实验方案分解为原子任务
2. 建立任务间依赖关系图
3. 估算资源需求与时间线
4. 输出 task plan spec

## Must NOT
- 不得遗漏任务依赖关系
- 不得忽略资源约束

## Output Contract
按 `schemas/ope/specs/task_plan.md` 模板产出，使用 Write 工具写入当前任务工作区的 `plan/task_plan.md`。

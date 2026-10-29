---
name: ope-experiment-execution
description: 执行实验并记录过程与结果
---

# 实验执行 (L2 — OPE)

## Goal
按照实验设计与实现方案执行实验，记录完整的执行过程与结果。

## Must Do
1. 按任务计划有序执行实验
2. 记录每次实验的参数、环境与结果
3. 异常情况及时记录并处理
4. 输出执行日志

## Must NOT
- 不得跳过实验参数记录
- 不得修改已完成的实验数据

## Output Contract
按 `schemas/ope/specs/execution_log.md` 模板产出，使用 Write 工具写入当前任务工作区的 `execute/experiments/execution_log.md`。

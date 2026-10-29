---
name: full-execution
description: 全数据集×全方法×全种子×全消融
---

# 全规模执行 (L2)

## Goal
全数据集×全方法×全种子×全消融

## Must Do
1. 按实验矩阵全规模运行所有实验组合
2. 收集并存储原始实验结果与日志
3. 输出 execution-full spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/03-experiment/execution-full.md` 模板产出，使用 Write 工具写入当前任务工作区的 `03-experiment/execution-full.md`。

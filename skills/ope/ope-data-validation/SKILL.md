---
name: ope-data-validation
description: 验证实验数据的完整性与有效性
---

# 数据验证 (L2 — OPE)

## Goal
对实验产出数据进行完整性与有效性验证，确保数据质量满足分析要求。

## Must Do
1. 检查数据完整性（缺失值、异常值）
2. 验证数据分布与预期一致性
3. 进行统计检验确认结果显著性
4. 输出数据验证报告

## Must NOT
- 不得跳过异常值检测
- 不得修改原始实验数据

## Output Contract
按 `schemas/ope/specs/data_validation.md` 模板产出，使用 Write 工具写入当前任务工作区的 `execute/data/data_validation.md`。

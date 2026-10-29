---
name: hypothesis-update
description: 根据证据修正或生成新假说
---

# 假说更新 (L2)

## Goal
根据证据修正或生成新假说

## Must Do
1. 根据实验证据修正、细粒化或生成新假说
2. 确保更新后的假说仍可证伪
3. 输出 hypothesis-update spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/04-report/hypothesis-update.md` 模板产出，使用 Write 工具写入当前任务工作区的 `04-report/hypothesis-update.md`。

---
name: reproducibility
description: 环境锁定、种子固定、版本控制
---

# 可复现性 (L2)

## Goal
环境锁定、种子固定、版本控制

## Must Do
1. 锁定软件环境（依赖版本、容器镜像）
2. 固定随机种子与可配置参数
3. 输出 reproducibility spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/02-method/reproducibility.md` 模板产出，使用 Write 工具写入当前任务工作区的 `02-method/reproducibility.md`。

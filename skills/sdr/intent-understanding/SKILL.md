---
name: intent-understanding
description: 从用户描述识别研究目标、领域、类型
---

# 意图理解 (L2)

## Goal
从用户描述识别研究目标、领域、类型

## Must Do
1. 解析用户自然语言输入，提取研究目标与领域关键词
2. 判定研究类型（实证/综述/方法/应用）
3. 输出结构化 research-config spec

## Must NOT
- 不得跳过前序 substage 的依赖
- 不得产出不含追溯 ID 的 spec

## Output Contract
按 `schemas/sdr/specs/research-config.yaml` 模板产出，使用 Write 工具写入当前任务工作区的 `research-config.yaml`。

---
name: dev-ai-coding
description: >-
  开发者使用 AI 编码工具修改代码时触发。
  当用户说"遵循项目ai coding规范"、"AI coding rules"、
  "AI coding 守则"、"让 AI 改代码"时使用本 skill。
  确保开发者了解项目的 AI coding 使用边界和约束。
  即使用户只是开始一个 AI 辅助开发会话，也应该主动提醒参考相关规则。
---

# Dev: AI Coding Guardrails

## When to use

开发者准备使用 AI coding 工具修改本项目代码。

## Rules source

Read before starting:

- `spec/collaboration/09_工程施工与脚本交付规范.md` -- allowed/forbidden actions, output requirements
- `spec/collaboration/03_任务推进与交接.md` -- collaboration baseline, Issue vs task spec

## Key boundaries

- One task at a time, no cross-module changes
- Read docs before modifying code
- Never hand-edit `.claude/skills/`
- Never commit secrets or private URLs
- Never hard-code domain-specific fields as the only path
- After completing, output: file list, impact assessment, suggested commit message

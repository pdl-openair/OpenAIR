---
name: dev-skill-with-code
description: >-
  开发者需要创建含可执行脚本的 Skill 时触发。当用户说"给 Skill 加脚本"、
  "写个 code skill"、"Skill 里加 Python 脚本"、"skill with code"、
  "脚本型 Skill"时使用本 skill。
  含脚本的 Skill 有更严格的约束：依赖管理、文件读写边界、安全规则、replay 要求。
  即使用户只是往已有 Skill 里加 scripts/ 目录，也应该触发。
---

# Dev: Skill with Code

## When to use

开发者需要创建或修改包含可执行脚本（Python/Node）的 Skill。

## Rules source

Read before creating:

- `skills/README.md` -- OpenAIR-specific code-skill requirements (deps, file boundaries, security, sync, replay)

This skill extends `openair-skill-guider` -- read those docs first for base structure rules.

## Pre-checks

1. Confirm whether the skill truly needs code (vs. prompt-only skill)
2. Validate dependency management (`requirements.txt` or `package.json`)
3. Check file read/write boundaries -- scripts must not access paths outside their scope
4. Ensure structured error returns and replay compatibility

## Output

Code-type skill with `scripts/` directory, dependency file, and replay test.

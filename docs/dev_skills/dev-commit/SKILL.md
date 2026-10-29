---
name: dev-commit
description: >-
  开发者准备提交代码时触发。当用户说"commit"、"提交"、"写 commit message"、
  "commit these changes"、"生成提交信息"、"帮我写 commit"时使用本 skill。
  基于 git diff 生成符合项目 commit 规范的提交信息。
  即使用户没有明确说"commit message"，只要涉及提交代码的操作都应该触发。
---

# Dev: Commit Message Generation

## When to use

开发者已暂存代码，需要生成符合项目规范的 commit message。

## Step 1: Read staged diff

1. Run `git diff --cached --stat` to see staged changes
2. Read the actual diff
3. From the diff, extract the key identifiers and values that changed (types, enums, field names, status values, directory names, etc.)

## Step 2: Consistency Check

**STOP — Do not proceed to Step 3 until this check is complete.**

Check whether the staged changes introduce inconsistencies elsewhere in the project. This prevents committing partial or contradictory changes.

### What to check

Use the key identifiers extracted in Step 1. Search the rest of the project for the same concepts to see if they are still aligned.

### How to handle inconsistencies

- **Same-type inconsistency** (e.g. a status value changed in one doc but not another doc; a type changed in code but not in the corresponding code): fix directly, using the staged (new) content as the source of truth. Stage the fixes before committing.
- **Cross-type inconsistency** (e.g. a type definition changed in code but the corresponding docs still describe the old shape): ask the user which direction to resolve before making changes. Present the specific conflict and the two options.

### Steps

1. Search the project for those identifiers/values in all relevant files (docs, code, mocks, fixtures, skills, configs). Note: `.trae/skills/` and `.claude/skills/` are sync mirrors managed by `scripts/sync_claude_skills.py` — do NOT manually check or fix them.
2. For each inconsistency found:
   - If both sides are the same type (doc↔doc, code↔code): fix the non-staged side to match the staged side, stage the fix
   - If the sides are different types (code↔doc, code↔fixture, etc.): ask the user how to resolve
3. Report a brief summary of what was checked and what (if anything) was fixed or needs user decision

### Gate: Consistency check confirmation

Before proceeding, confirm ALL of the following:

- [ ] Key identifiers have been extracted from the diff
- [ ] Project-wide search has been performed for those identifiers
- [ ] All found inconsistencies have been resolved or escalated
- [ ] A brief check summary has been reported

**Only after all items are confirmed, proceed to Step 3.**

## Step 3: Generate commit message

### Rules source

Read before generating:

- `spec/collaboration/git_commit_rules.md` -- format, type, scope, summary rules

### Determine commit

From the actual diff, determine type, scope, and summary.

### Co-author（必须）

如果本次 commit 有 AI coding 工具参与，必须在 commit message body 末尾追加 `Co-authored-by` 行。

格式：
```text
<type>(<scope>): <summary>

Co-authored-by: qoder <qoder@users.noreply.github.com>
```

可用 Agent 标识：
- `qoder <qoder@users.noreply.github.com>`
- `cursor <cursor@users.noreply.github.com>`
- `claude-code <claude-code@users.noreply.github.com>`
- `gpt <gpt@users.noreply.github.com>`

规则：
- 根据实际参与的 AI 工具选对应行，可多个并列
- 如果是纯人工 commit 则不加
- Agent 在生成 commit message 时应自动追加自身标识

### Output

Single line: `<type>(<scope>): <summary>`
Optional body with Co-authored-by trailer.

If changes span multiple concerns, recommend splitting into separate commits.

# Git 提交说明规范

## 1. 文档作用

本文用于规范 OpenAIR 团队的 Git commit message 写法，方便多人协作、代码审查、问题回溯和版本管理。

适用对象：

- 团队成员；
- Cursor / Qoder / Claude Code 等 AI coding 工具；
- 所有 OpenAIR 仓库提交。

## 2. 基本原则

- 一次提交只表达一个清楚变更。
- 提交说明要具体，不写空泛词。
- 提交说明必须基于真实 diff，不凭任务描述猜。
- 如果改动很多，优先拆成多个 commit。
- commit message 不替代 PR 说明。

## 3. 提交格式

统一使用：

```text
<type>(<scope>): <summary>
````

示例：

```text
docs(skills): add code skill rules
feat(workbench): add task input entry
fix(api): record failed claude execution error
chore(repo): update docs navigation
```

[经典注记] 本条 ≈ Conventional Commits（conventionalcommits.org 规范）——差异：本仓 type 集合与禁用词表是自家裁剪（8 类型 + 禁 update/modify 等），scope 为建议值清单而非自由词，另强制 AI 参与带 Co-authored-by（见第 11 节）。

[SPEC-ENG-SPEC-PROC-03] 本条已机器强制：check_commit_msg.py（hook 即时 + CI 兜底，L0+L1；登记见 `spec/enforcement/enforcement-matrix.md`）。

## 4. type 取值

只使用以下 type：

| type       | 用途               |
| ---------- | ---------------- |
| `feat`     | 新增功能             |
| `fix`      | 修复问题             |
| `docs`     | 文档修改             |
| `refactor` | 重构，不改变外部行为       |
| `style`    | 样式或格式调整          |
| `test`     | 测试、fixtures、回放样例 |
| `chore`    | 构建、配置、脚本、目录整理    |
| `revert`   | 回滚提交             |

禁止使用：

```text
update
modify
change
optimize
misc
wip
```

## 5. scope 建议

常用 scope：

```text
workbench
task-board
task-detail
situation-board
api
claude-sdk
skills
skill-management
fixtures
shared
docs
collaboration
vibe-coding
repo
```

如果不确定，用最主要的修改范围。

## 6. summary 写法

summary 要说明“具体改了什么”。

推荐：

```text
docs(skills): add replay test rules
feat(api): add task creation endpoint
fix(task-board): correct stage status display
chore(fixtures): add recorded run directory
```

不推荐：

```text
docs: update
fix: fix bug
chore: modify files
feat: add things
```

## 7. AI coding 工具生成提交说明

让 Cursor / Qoder / Claude Code 生成 commit message 时，使用这个提示词：

```md
请根据当前 git diff 生成一条符合 OpenAIR 规范的 commit message。

要求：
- 使用格式：<type>(<scope>): <summary>
- type 只能使用 feat/fix/docs/refactor/style/test/chore/revert
- scope 根据实际修改范围判断
- summary 必须具体，不能写 update/modify/optimize
- 必须基于真实 diff，不要编造不存在的修改

请输出：
1. 推荐 commit message
2. 判断依据：本次主要修改了哪些文件
```

生成前应先查看：

```bash
git status
git diff --stat
git diff
```

如果文件已经暂存，则查看：

```bash
git diff --cached --stat
git diff --cached
```

## 8. 什么时候要拆 commit

以下情况建议拆开：

* 文档和业务代码混在一起；
* 前端和后端是两件独立修改；
* Skill 规范和 Skill 实现混在一起；
* fixtures 和真实接口修改混在一起；
* 修 bug 的同时又做了重构。

示例：

```text
docs(skills): add skill registry rules
chore(fixtures): add task detail sample data
fix(api): handle failed claude execution result
```

不要合成：

```text
chore(repo): update multiple files
```

## 9. PR 与 commit 的区别

commit message 只说明单次提交。

PR 说明需要额外写清：

* 本次 PR 改了什么；
* 没改什么；
* 如何验收；
* 是否影响数据契约；
* 是否影响 Skill registry；
* 是否影响 fixtures；
* 是否有后续 TODO。

## 10. 合并前检查

提交前确认：

* type 合法；
* scope 准确；
* summary 具体；
* 与 diff 一致；
* 没有把无关修改混在一起；
* 没有提交密钥、token、私有接口地址。

[SPEC-ENG-SPEC-SEC-01] 该检查项已机器强制：gitleaks（pre-commit workspace 扫描 + CI secret-scan job，L0+L1；登记见 `spec/enforcement/enforcement-matrix.md`）。

## 11. Co-authored-by（AI 协作署名）

当 commit 由 AI coding 工具（Cursor / Qoder / Claude Code / GPT 等）参与生成时，必须在 commit message body 末尾追加 `Co-authored-by` trailer。

格式示例：

```text
docs(collaboration): add co-author convention

Co-authored-by: qoder <qoder@users.noreply.github.com>
```

可用 Agent 标识（按实际使用选择，可多个并列）：

```text
Co-authored-by: qoder <qoder@users.noreply.github.com>
Co-authored-by: cursor <cursor@users.noreply.github.com>
Co-authored-by: claude-code <claude-code@users.noreply.github.com>
Co-authored-by: gpt <gpt@users.noreply.github.com>
```

规则：

* 纯人工 commit 不加；
* AI 辅助生成的 commit 必须加对应 Agent；
* 一次 commit 涉及多个 AI 工具时，每个各占一行；
* Co-authored-by 行与 summary 之间空一行（即放在 body 区域）；
* GitHub 会自动识别并在 commit 页面展示贡献者头像。

[SPEC-ENG-SPEC-PROC-03] 本条与第 3 节同属一个登记行：AI 参与必带 Co-authored-by 由 check_commit_msg.py 一并校验。

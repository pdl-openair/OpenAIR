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

[经典注记] 本条 ≈ Conventional Commits（conventionalcommits.org 规范）——差异：本仓 type 集合与禁用词表是自家裁剪（8 类型 + 禁 update/modify 等），scope 为建议值清单而非自由词，另要求 AI 参与在正文说明工具与模型（见第 11 节）。

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

## 11. AI 参与说明（不强制 trailer）

AI 参与的提交或 PR 不要求固定格式的 `Co-authored-by` trailer，也不维护允许标识清单。作者应在 commit body 或 PR 描述中用一行文字说明使用的工具与模型，例如：

```text
AI 协助：omp / deepseek-flash
```

规则：

* 纯人工提交无需说明；
* AI 参与时说明工具与模型即可，放在 commit body 或 PR 描述由作者选择；
* 不要求 `Co-authored-by`，不校验标识清单，不要求邮箱或贡献者头像；
* 已有提交中的历史 trailer 保留，不追溯改写。

[SPEC-ENG-SPEC-PROC-03] 本条与第 3 节同属一个登记行：提交信息格式由 check_commit_msg.py 校验；AI 参与说明为文本约定，不做机器强制。

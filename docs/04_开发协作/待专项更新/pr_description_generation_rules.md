# PR 描述生成规范

## 1. 文档作用

本文用于指导 Qoder / Cursor / Claude Code 根据真实 git diff 生成 Pull Request 标题和描述。

目标是让 AI coding 工具先生成一版可用 PR 文案，人只需要检查、删改和确认。

本文不替代 `.github/pull_request_template.md`，而是用于自动填充该模板。

## 2. 基本原则

- 必须基于真实 diff 生成，不得凭任务描述猜。
- 不夸大改动，不写 diff 中不存在的内容。
- 不写空泛描述，如“优化项目结构”“完善文档体系”。
- 必须说明本次改了什么、没改什么、怎么验收。
- 如果有风险、未提交文件、后续 TODO，必须写清楚。
- 不要自动创建 PR，除非用户明确要求。
- 默认只生成 PR 标题和 PR 描述，供人工复制粘贴确认。
- **精简优先**：整份 PR 描述控制在 1 屏（约 40 行）以内。能用 5 个字说清的不写 50 个字；commit log 已说的不重复；模板小节不适用写「无 / 暂无」，不展开凑数；「主要修改文件」按目录归类不粘贴全量路径；无风险直接写「暂无」。

## 任务关联规则

生成 PR 描述时，必须优先判断本次 PR 是否对应 GitHub Issue。

- 如果用户提供了 Issue 编号，必须在「关联任务」中写 `Closes #xx`。
- 如果只是关联任务但不希望自动关闭，写 `Ref #xx`。
- 如果任务来自飞书，写飞书任务名称或链接，不使用 `Closes`。
- 如果无法确定 Issue 编号，不要编造，写 `N/A`，并在输出中提醒用户补充。
- `Closes #xx` 必须出现在 PR 描述正文中，不能只写在标题里。

## 3. 生成前必须读取

生成 PR 描述前，先执行并阅读：

```bash
git status
git log --oneline -5
git diff --stat origin/<base-branch>...HEAD
git diff --name-only origin/<base-branch>...HEAD
````

如无法确定 base branch，先读取当前分支和远端分支：

```bash
git branch --show-current
git branch -r
```

常见 base branch：

```text
dev
main
master
```

以实际 PR 页面显示的 base branch 为准。

## 4. 输出内容

必须输出：

```md
# PR 标题

<推荐标题>

# PR 描述

<完整 PR 描述>
```

PR 标题建议使用 commit 风格：

```text
<type>(<scope>): <summary>
```

示例：

```text
chore(repo): restructure docs layout and add skills fixtures scaffold
docs(collaboration): add PR description generation rules
feat(workbench): add task creation entry
fix(api): record failed Claude execution error
```

## 5. PR 描述模板

生成时使用以下结构：

```md
## 改了什么

-

## 关联任务

<!--
如本 PR 用于关闭 GitHub Issue，必须写：
Closes #xx

如只是关联，不关闭，写：
Ref #xx

如来自飞书任务，写：
飞书任务：xxx / 链接

无明确任务时写：
N/A
-->

Closes #

## 本次没有改什么

-

## 主要修改文件

-

## 怎么验证

-

## 是否影响数据 / 接口 / Mock

- [ ] 不影响
- [ ] 影响 shared 类型
- [ ] 影响 API 契约
- [ ] 影响 mock / fixtures 数据
- [ ] 影响任务状态 / 过程产物
- [ ] 影响 Skill registry
- [ ] 影响 `.claude/skills/`
- [ ] 已同步更新相关文档
- [ ] 需要进一步确认，说明：

## 风险与注意事项

-
```

如果某项不适用，写 `无` 或 `N/A`，不要留空。

## 6. “改了什么”怎么写

只写主要变化，不列全量 diff。

推荐写法：

```md
本次主要完成 OpenAIR 仓库文档与工程协作骨架整理：

- 更新根 README、CONTRIBUTING 和 docs 入口说明
- 新增 GETTING_STARTED，提供新成员快速上手路径
- 梳理 Skill、fixtures、vibe coding、协作规范文档
- 新增 skills/ 源目录结构和 .claude/skills 同步说明
```

不推荐：

```md
- 修改很多文件
- 优化项目
- 完善文档
- 调整结构
```

## 7. “本次没有改什么”怎么写

必须写清边界，防止 reviewer 误解。

示例：

```md
- 未开发前端页面功能
- 未实现后端业务接口
- 未接入真实 Claude Agent SDK 调用
- 未实现真实科研 Skill
- 未修改数据库结构
- 未提交 design/ 下未跟踪的设计文件
```

## 8. “主要修改文件”怎么写

按目录归类，不要粘贴全量路径。

示例：

```md
- `README.md`
- `CONTRIBUTING.md`
- `docs/`
- `skills/`
- `.claude/`
- `fixtures/`
- `scripts/`
```

如果只改少量文件，可以列具体文件。

## 9. “怎么验证”怎么写

根据变更类型生成。

文档 / 骨架类 PR：

```md
- 检查 README 是否能说明项目定位和入口
- 检查 docs/03_工程/待专项更新/GETTING_STARTED.md 是否能指导新人快速开始
- 检查 Skill、fixtures、Claude 相关说明是否互相冲突
```

前端类 PR：

```md
- 启动前端
- 打开对应页面
- 检查空状态、加载态、失败态
- 检查是否能读取 fixtures 或接口数据
```

后端类 PR：

```md
- 启动后端
- 检查 /health
- 调用相关 API
- 检查状态、产物、能力调用记录是否写入
```

Skill 类 PR：

```md
- 检查 skill.yaml 和 SKILL.md
- 检查 input/output schema
- 执行 replay 样例
- 检查 registry 是否同步
```

## 10. 影响范围判断

根据 diff 勾选，不确定时写“需要确认”。

判断参考：

| 变更                        | 需要勾选                  |
| ------------------------- | --------------------- |
| 修改 `packages/shared`      | 影响 shared 类型          |
| 修改 API 路由或返回结构            | 影响 API 契约             |
| 修改 `fixtures/`            | 影响 mock / fixtures 数据 |
| 修改任务状态字段                  | 影响任务状态 / 过程产物         |
| 修改 `skills/registry.yaml` | 影响 Skill registry     |
| 修改 `.claude/skills/`      | 影响 `.claude/skills/`  |
| 修改 docs                   | 已同步更新相关文档             |

## 11. 风险与注意事项

必须如实写。

常见风险：

```md
- 本次变更文件较多，建议重点检查是否仍有旧目录口径
- 本次只新增骨架，不代表真实能力已接入
- design/ 目录未纳入本次提交，后续需单独确认设计文件管理方式
- 新增脚本尚未接入 CI
```

没有风险时写：

```md
暂无明显风险。
```

## 12. 推荐给 Qoder / Cursor 的提示词

````md
请根据当前分支相对 base 分支的真实 diff，生成一份可直接粘贴到 GitHub 的 PR 标题和 PR 描述。

请遵守：

- `CONTRIBUTING.md`
- `spec/collaboration/git_commit_rules.md`
- `docs/04_开发协作/待专项更新/review_and_acceptance_rules.md`
- `docs/04_开发协作/待专项更新/pr_description_generation_rules.md`

请先执行并阅读：

```bash
git status
git branch --show-current
git diff --stat origin/<base-branch>...HEAD
git diff --name-only origin/<base-branch>...HEAD
````

如果 `<base-branch>` 不确定，请先告诉我，不要猜。

输出要求：

1. 给出推荐 PR 标题
2. 给出完整 PR 描述
3. 描述必须包括：

   * 改了什么
   * 本次没有改什么
   * 主要修改文件
   * 怎么验证
   * 是否影响数据 / 接口 / Mock
   * 风险与注意事项
4. 不要创建 PR
5. 不要 push
6. 不要修改文件
7. 不要编造 diff 中不存在的内容

````

## 13. 可选：生成 PR_BODY.md

如果用户要求生成文件，可以输出到：

```text
.tmp/PR_BODY.md
````

但默认不要自动提交该文件。

如果需要用 GitHub CLI 创建 PR，可由用户确认后再执行：

```bash
gh pr create --title "<PR title>" --body-file .tmp/PR_BODY.md
```

未经用户确认，不要自动创建 PR。

## 14. Reviewer 指派

PR 创建后，必须 assign 至少一名 reviewer（不能是自己）。

### 选择规则

- 优先选改动目录的 CODEOWNER
- 其次选 spec / Issue 中标注的协作人
- 再次选团队中熟悉该模块的成员
- **不能 assign PR 作者自己**

### 指派方式

```bash
gh pr edit <pr_number> --add-reviewer <reviewer_github_username>
```

### 飞书通知

指派后通过 `lark-cli` 发送飞书消息提醒 reviewer（需 lark-cli 可用）：

```text
📬 你被指派为 PR #{pr_number} 的 Reviewer
标题：{pr_title}
链接：{pr_url}
提交人：{author}
请及时审阅，谢谢！
```

如果 lark-cli 不可用，手动通知或在 PR 中 @reviewer。

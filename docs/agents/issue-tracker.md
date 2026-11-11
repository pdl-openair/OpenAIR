# Issue tracker: GitHub

本仓库的 issue 与任务要求存放在 GitHub Issues，操作使用 `gh` CLI。本文件只写工具用法与状态载体。issue 状态机、DoR 门禁与验证层级以 [spec/workflows/issue-workflow.md](../../spec/workflows/issue-workflow.md) 为准，PR 证据字段以 [spec/workflows/pr-workflow.md](../../spec/workflows/pr-workflow.md) 为准。两份 spec 与本文件冲突时以 spec 为准。

## 约定

- 创建 issue 前先搜文档、README、既有 issue 与 PR。已有答案或进行中的讨论，评论到原帖，不发新帖。
- 正文按 `.github/ISSUE_TEMPLATE/` 对应模板填写：缺陷用 `bug.md`，任务与需求用 `feature.md`，阶段发布用 `release.md`。
- 创建：`gh issue create --title "..." --body "..."`。多行正文用 heredoc。
- 读取：`gh issue view <number> --json number,title,body,labels,comments,state,author,createdAt`。用 `jq` 过滤评论。
- 列表：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`。按需加 `--label`、`--state`。
- 评论：`gh issue comment <number> --body "..."`。
- 添加标签：`gh issue edit <number> --add-label "..."`。移除标签：`gh issue edit <number> --remove-label "..."`。
- 关闭：`gh issue close <number> --comment "..."`。

从 `git remote -v` 推断仓库，`gh` 在 clone 内运行会自动识别。

## AI 贡献标记

评论、issue、PR 由 AI 生成或辅助时，正文最后一行加标记，语言跟正文一致。中文用 `> 本评论由 <Harness> 的 <模型> 完成。`，整段只是转述维护者结论时写 `辅助完成`。英文用 `> Written by <Harness> using <model>.`。Harness 与模型填当前会话实际值，取不到写 `unknown`。

提交信息里的 AI 署名用 `Co-authored-by`，规则见 [spec/collaboration/git_commit_rules.md](../../spec/collaboration/git_commit_rules.md) 第 11 节。issue 标题按 `.github/ISSUE_TEMPLATE/` 的模板加 `[bug]`、`[task]` 或 `[release]` 前缀。PR 标题按 `/open-pr` 约定加 `[AI Generated][类型]` 前缀。

## 工作状态

不使用项目看板与项目字段。issue 的工作状态由标签表达，施工状态机见 spec/workflows/issue-workflow.md。分诊状态标签见 [triage-labels.md](triage-labels.md)，它与施工状态是两套标签，不互相替代。

## Pull request 作为分诊渠道

否。本仓库不把外部 PR 当作功能请求入口。

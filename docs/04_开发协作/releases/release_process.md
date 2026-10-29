# 阶段发布流程

发阶段说明前，整理 `STATUS.md`、`CHANGELOG.md`、milestone 和 Release Notes。
让新人、旧人和协作者快速知道本轮做了什么、怎么验证、还有什么风险。

## 什么时候需要发一轮

出现这些情况时发阶段说明：

- 准备 demo、汇报或阶段交付；
- 一批重要功能或 bug 已经合并；
- 当前状态发生明显变化；
- 实验、评测或产品检查有新结果；
- 需要把 milestone 中的 Issue/PR 做一次归档。

## 发布前检查

- [ ] `STATUS.md` 已更新：当前到哪了、风险是什么、下一步做什么。
- [ ] `CHANGELOG.md` 已更新：本阶段成果、验证记录、仍需收口事项。
- [ ] milestone 已整理：本轮包含什么、不包含什么、哪些是 follow-up。
- [ ] release blocker 已标清楚。
- [ ] 验证记录能找到：命令、harness、截图、报告或手工检查说明。
- [ ] 已写明“当前不能下的结论”。

## Release Notes 推荐结构

```markdown
## 本轮目标

一句话写本轮目标。

## 阶段成果

- ...

## 验证记录

- ...

## 不包含范围

- ...

## 已知风险

- ...

## 后续事项

- ...

## 关联 Issue / PR / 文档

- ...
```

## tag 命名

现阶段用“方向 + 日期”，不要过早引入复杂版本号。

示例：

```text
openair-b7-rx-20260708
demo-f1c0-baseline-20260703-typed
opentrajectory-eval-20260708
```

## milestone 怎么用

milestone 回答一个问题：这一轮包含什么？

每个 milestone 至少写：

- 本轮目标；
- 包含范围；
- 不包含范围；
- 阻塞发布的问题；
- 本轮不做但后续要做的事项。

## labels 怎么用

只用稳定标签，不要给每个内部代号都建 label。

常用：

- `type/bug`、`type/feature`、`type/docs`
- `priority/P0`、`priority/P1`、`priority/P2`
- `status/ready`、`status/in-progress`、`status/review`、`status/done`
- `release/blocker`、`release/follow-up`
- `type/known-issue`、`type/regression`

## GitHub Release 操作

准备好 Release Notes 后：

```powershell
git tag openair-b7-rx-20260708
git push origin openair-b7-rx-20260708
```

使用 GitHub CLI：

```powershell
gh release create openair-b7-rx-20260708 --title "OpenAIR B7 RX - 2026-07-08" --notes-file .\tmp\release-notes.md
```

GitHub 页面：

```text
Releases → Draft a new release → 选择 tag → 粘贴 Release Notes → Publish release
```

## 最小原则

- Release Notes 不写成 commit log。
- PR 号可出现，但不要当主语。
- 一定写验证记录和已知风险。
- 不确定的结论写“不确定”，不要包装成已完成。

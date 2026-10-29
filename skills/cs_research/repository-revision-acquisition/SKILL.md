---
name: repository-revision-acquisition
description: >-
  在当前任务受控工作区中真实获取一个 Git repository，将上游明确给定的 revision
  解析为确定 commit，检出后重新读取并验证 HEAD，并返回最小可追溯执行结果与凭据。
---

# 代码仓库与版本获取

## 使用条件

仅在上游已经明确提供以下两个输入时使用：

- `repository`：Git repository URL
- `revision`：需要固定的 Git revision

## 职责

1. 在当前任务工作区下固定使用 `repository/` 目录获取代码仓库。
2. 真实执行 Git，将请求的 revision 解析为确定 commit。
3. 检出该 commit。
4. 检出后重新读取真实 `HEAD`。
5. 只有 `resolved_commit_sha == verified_head` 时才成功。
6. 返回仓库地址、请求版本、解析 commit、验证 HEAD、工作区相对代码位置和执行凭据。

## 边界

本 Skill 不负责数据、模型、checkpoint、环境、实验、指标、论文理解、Research Package、ResearchDesign、Workflow、ScientificState、OT、PVD 或通用失败恢复。

输入不得提供工作区路径或目标目录；代码只能写入 Runtime 为本次任务指定的受控工作区。

## 失败关闭

以下情况必须明确失败，不能回退到默认分支或猜测其他版本：

- repository 不可获取；
- revision 无法解析为 commit；
- 对普通远程分支名，只允许映射到 clone 后同名的 `origin` 远程分支引用，不得替换成其他 branch、tag 或 revision；
- checkout 失败；
- HEAD 无法读取；
- HEAD 与解析 commit 不一致；
- 工作区或目标目录边界非法；
- Git 不可用；
- 输入合同非法。

## 输出原则

只返回会影响代码版本可复现性和执行追溯的高价值事实。数值、路径身份和 Git 执行结果由代码确定，不交给模型重写。

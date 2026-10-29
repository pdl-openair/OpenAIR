---
name: code-identity-acquisition
description: 论文复现 Workflow 的代码身份固定研究工作：消费已进入 ScientificState 的 reproduction_repository 与 reproduction_revision，复用正式代码版本获取能力形成可追溯代码身份。
---

# 代码版本固定

只消费上一研究工作已经形成并被接受的已确认并通过来源核验的正式事实 `reproduction_repository` 与 `reproduction_revision`。缺少必要事实时当前研究工作不得启动。

本节点复用既有 `repository_revision_acquisition` 原子能力，不自行选择默认分支、不静默升级 revision。

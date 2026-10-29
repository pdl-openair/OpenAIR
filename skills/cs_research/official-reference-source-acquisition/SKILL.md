---
name: official-reference-source-acquisition
description: 复用上游已经固定并校验的官方 Git 仓库工作区，读取有限的文本型官方资料，固定文档路径、内容哈希和原文，供后续 reference 科研判断使用。
---

# 官方参考来源读取

## 输入 Authority

只接受上游 `repository_revision_acquisition` 已形成的正式 Artifact（`code_source`），其中至少包含：

- 官方 `repository`；
- `requested_revision`；
- `resolved_commit_sha` / `verified_head`；
- `workspace_relative_path`。

## 职责

1. 读取上游已经真实取得的仓库工作区，不重复 clone，也不重新选择版本。
2. 再次核验当前仓库 `HEAD` 与上游正式 Artifact 的 immutable commit 完全一致。
3. 只收集该 commit 中受控文本文件（Markdown / reStructuredText / text / AsciiDoc）。
4. 对每份文档固定路径、内容 SHA256、稳定 `document_id` 和原文。
5. 对文档数量、单文档大小和总输入大小设置硬上限，避免把整个仓库交给后续模型。

## 边界

- 不访问新的外部来源；
- 不判断哪个结果与当前实验匹配；
- 不抽取指标；
- 不解释论文或 README；
- 不决定比较规则或 tolerance；
- 不读取 actual metrics；
- 不执行仓库代码；
- 不使用文件名、关键词或正则替代科研判断。

文件类型筛选只用于限定“哪些文本可以安全进入来源包”，不是科研相关性筛选。实验条件匹配和指标语义由后续 LLM Skill 负责。

## 失败原则

工作区不存在、HEAD 与上游固定 commit 不一致、没有可用文本资料或来源包超过安全边界时明确失败；不得回退到默认分支、latest 或其他版本。

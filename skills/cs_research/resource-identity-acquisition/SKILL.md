---
name: resource-identity-acquisition
description: >-
  在当前任务受控工作区中真实取得上游已经明确的 Hugging Face 模型、数据集、checkpoint
  或仓库内文件，将请求 revision 解析为确定 commit，并在正式暴露资源前验证实际落地与预期身份。
---

# 复现资源获取与身份固定

## 使用条件

仅在上游已经明确给出一份或多份 Hugging Face 资源定位后使用。当前支持：

- Hugging Face model / dataset repository 的完整 snapshot；
- Hugging Face model / dataset repository 内的单个文件。

上游负责决定“该用哪些资源、各自用于什么”；本 Skill 只负责把已经确定的资源真实取得并逐份固定身份。单资源输入继续兼容；多资源输入按清单逐份处理。

## 职责

1. 在当前任务受控工作区的 `resource/` 根目录下，为每份 immutable Resource 使用独立、确定性的正式位置；多份资源不得互相覆盖。批量输入只表示一次提交多份请求，不形成新的“资源集合”身份。
2. 使用官方 `huggingface_hub` 客户端解析请求 revision。
3. 以解析得到的 immutable commit 作为实际来源版本，并读取该 commit 的官方文件清单，再执行真实下载。
4. 完整 snapshot 必须与该清单逐文件一致后才可正式暴露；snapshot 使用来源 commit 作为主身份，单文件同时计算实际文件 SHA256。
5. 对上游提供的 expected commit / expected SHA256 做确定性验证。
6. 只有获取、落地检查和身份验证全部完成后，才把 staging 资源原子暴露到该 Resource 自己的 `resource/<deterministic-key>/...` 正式位置。
7. 失败时清理当前资源的 staging；清理失败必须显式返回。批量处理中已经成功固定的其他资源继续保留为成功事实，每一份资源分别返回状态和失败原因。

## 边界

本 Skill 不负责：

- 搜索资源或决定 checkpoint；
- 执行仓库自带下载脚本；
- archive 解压、数据预处理、模型加载、环境安装、实验执行；
- Git repository / revision 获取；
- OpenAIR Artifact / provenance / Workflow / runtime 正式集成。

当前没有普通 HTTP、S3、Google Drive 或通用云存储 provider。SimCSE 只是需求校准案例，生产代码不得包含 SimCSE、SentEval 或 Princeton NLP 特判。

## 身份规则

- Hugging Face snapshot：`repo_id + repo_type + resolved_commit_sha`。
- Hugging Face 单文件：`repo_id + repo_type + resolved_commit_sha + filename + sha256`。
- `requested_revision` 只是请求，不得当作实际版本。
- URL、文件名、HTTP 成功状态不得单独当作资源身份。

## 失败关闭

以下情况必须失败且不得暴露正式资源：

- 输入合同非法；
- workspace 非 Runtime 授权目录；
- `resource/` 根目录非法，或同一正式 Resource 的确定性目标已经存在而发生冲突；
- `huggingface_hub` 不可用或低于已包含 `local_dir` 路径逃逸修复的 1.26；
- repository / revision / file 不可取得；
- Provider 未返回合法 commit 或完整文件清单；
- 实际 snapshot 缺少清单中的任何文件；
- 实际落地为空、不在 staging 内或包含逃逸 workspace 的符号链接；
- expected commit / SHA256 不匹配；
- 失败现场清理失败。

## 凭据

凭据不属于 Skill 输入。需要认证时只使用 `huggingface_hub` 的既有凭据机制；token 不进入结果或错误输出。


## 显式默认版本观测

revision 字段仍必须提供。字符串按原值请求，不把 unknown 等字面量转换为 main。显式 null 表示请求官方客户端观测该目标的默认版本；先读 Provider 返回的实际 commit，再以该 immutable commit 读取清单并下载。requested_revision 保留 null，resolved_commit_sha 记录真实返回值，不能将默认版本观测说成官方来源指定版本或已验证科研等价。固定 expected_commit_sha / expected_sha256 保留，任一不匹配仍不能暴露正式资源。

---
name: exploratory-run
description: 在代码、资源、环境和单次实验目标都已明确后，使用已验证环境真实启动一次受控实验，取得真实退出状态与有界 stdout/stderr，并形成稳定执行记录。
---

# 受控实验执行

## Goal

只完成一个已经定义好的实验执行：

`明确程序与参数 → 固定工作目录 → 使用已验证环境 → 真实启动进程 → 受控等待 → 取得真实退出状态 → 固定 stdout/stderr → 写 execution record`

本 Skill 不判断实验“理论上应该成功”，只记录实际进程发生了什么。

## Input

- `executable`：相对于正式 `environment/` 的实际可执行程序路径；
- `argv[]`：直接传给进程的参数数组；
- `working_directory`：Runtime 受控 workspace 内已经存在的工作目录；
- `timeout_seconds`：本次单次执行的明确超时；
- `environment_variables`：可选、明确的环境变量覆盖；值只用于执行，不写入正式 execution record；
- `resource_bindings[]`：可选。只有本次执行明确依赖正式 Resource 时提供；每项给出 `model | dataset` 用途和正式 Resource 成功记录。直接路径交付使用 `argv_index`，对应 argv 位置必须是受控占位符 `{{openair-resource:model}}` / `{{openair-resource:dataset}}`；归档型数据可显式使用 `delivery.kind=tar_extract_working_directory`，只把已固定、带 SHA256 的 tar 文件安全展开到本次 `working_directory`；需要由受控 Python 运行时形成实际读取证据时，可显式声明 `consumption_probe.kind=python_file_open_read`。

不接受 shell command 字符串。`executable` 与 `working_directory` 不接收绝对路径。无 Resource 依赖时，`argv` 继续原样传递；Resource 的交付方式必须由输入合同明确给出，执行器不根据文件名、扩展名、程序名或案例名猜测。敏感凭据应通过 `environment_variables` 提供。

## Execution

1. 只在 `OPENAIR_ADAPTER_EXECUTION_CWD` 明确授权且当前目录完全一致的 workspace 内运行。
2. 必须存在 S3A 形成的 `environment/environment-record.json`，且其状态、schema 和环境指纹合法。
3. `working_directory` 必须解析到 workspace 内现有目录；路径逃逸失败关闭。
4. `executable` 必须位于正式 `environment/` 下，实际文件必须存在；中间目录不允许通过符号链接逃逸。
5. 若存在 `resource_bindings`，先确定性核对正式 Resource 记录、用途、固定 workspace 路径和实际落地对象；任何一项不足都在进程启动前失败关闭。
6. 直接路径交付只替换明确声明的 argv 占位符。tar 交付会重新核对正式文件 SHA256，只接受普通文件/目录，限制成员数量、单文件大小和总展开大小，拒绝路径穿越、链接、设备文件和覆盖已有文件；进程启动前把本次展开文件的路径、大小和 SHA256 形成确定性交付清单并保存在 execution 目录，执行结束后只撤回本次新增文件/目录，不改已有代码。交付成功仍只证明 delivery，不证明实际消费。
7. 对每个已绑定 Resource，执行器提供本次运行唯一的消费证据文件位置。目标程序若已有正式消费凭证协议，可继续写入 `openair-resource-consumption-evidence/v1`；显式启用 `python_file_open_read` 时，受控 Python 启动钩子只记录真正以读取方式打开且命中本次受控资源范围的文件，并形成 v2 消费证据。父执行器会重新把访问路径映射到本次正式 Resource / tar 交付清单；没有证据时保持 `unverified`，不因 argv、解包或程序成功而自动升级。
8. stdout / stderr 分开持续读取，各自最多保留 64 KiB；超出部分继续排空但不继续占用内存。
9. stderr 非空不代表失败；最终成功只由真实进程启动状态、timeout / termination 状态和 exit code 决定。
10. timeout 时必须终止进程树：Windows 使用 `taskkill /T /F`，POSIX 使用独立进程组；无法确认进程树终止时返回 `termination_failed`。
11. 所有已启动/尝试启动的执行都形成实际时间、持续时间、exit code、stdout/stderr 等事实；execution record 原子写入 workspace 下的 `execution/<execution-id>/execution-record.json`。
12. 环境变量值不写入 execution record；可识别的 secret/token 类环境值若同时出现在 `argv`、stdout 或 stderr 中，正式记录统一替换为 `<redacted>`。

## Result Semantics

- `succeeded`：进程真实启动，未超时，exit code = 0；
- `process_start_failed`：可执行文件已通过前置检查，但操作系统无法启动；
- `non_zero_exit`：进程真实结束且 exit code != 0；
- `timeout`：达到超时并已确认终止进程树；
- `termination_failed`：timeout 后无法确认目标进程树已被终止。

输入、workspace、环境记录、工作目录或 executable 前置条件非法时 fail closed，不伪造一次已发生的实验。

## Output

正式 execution record 至少包含：

- 实际 executable 与 argv；
- 实际 working directory；
- 使用的正式环境路径、环境记录和环境指纹；
- 有 Resource 绑定且进程真实启动时，记录本次计划资源和实际 delivery（argv 路径或受控 tar 展开清单摘要）；仅有 delivery 时 `verification=unverified`，不生成 `actual_usage`；只有可重新校验的目标程序凭证或显式受控文件读取证据存在时，才记录 `actual_usage` 并得到 `verified` 或 `mismatch`；进程未启动时不生成 resource usage；
- 开始、结束、duration；
- exit code；
- timeout / termination 状态；
- 有界 stdout / stderr 及截断事实；
- 最终 execution status；
- execution record 的 workspace 相对位置。

这些字段全部来自实际路径解析、正式 Resource 记录、环境记录、真实进程事实和可确定性核验的消费证据。argv/env/path 注入只属于 delivery；资源使用关系由确定性代码形成，不要求 LLM 生成或判断，也不根据参数名或程序名字猜测消费语义。

## Responsibility Boundary

本 Skill 只负责单次实验执行与执行事实固定。

不负责 Git / resource 获取、环境建立、代码生成或修改、Artifact 采集、指标解析、论文结果比较、baseline comparison、Notebook / DAG / scheduler / distributed training / GPU 调度、远程执行、worker、durable runtime、Workflow、OT、PVD 或正式 OpenAIR 集成。

SimCSE 仅作为真实验收案例，生产实现不得包含 SimCSE / SentEval / Princeton 特判。

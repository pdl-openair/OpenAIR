---
name: environment-setup
description: 在上游已经明确 Python、依赖、关键导入和可选目标运行就绪探针后，真实建立隔离 Python 环境，验证其可用性，并从实际环境读取事实形成稳定环境身份记录。
---

# Environment Setup

## Goal

把上游给定的环境要求真正落地为后续实验可以信任的隔离环境，而不是生成安装说明或把请求依赖抄成“实际环境”。当上游已经明确目标程序时，环境只有通过该目标的低成本真实运行就绪探针后，才可形成带 `runtime_readiness=passed` 的正式环境记录。

正式成功链：

`明确环境要求 → 建立隔离环境 → 安装必要依赖 → pip 一致性验证 → 关键模块导入验证 → 目标程序运行就绪探针（可选）→ 读取实际环境事实 → 固定环境身份 → 写正式环境记录`

任何一步失败时，本轮创建的 `environment/` 必须清理；只有全部已配置验证完成后才允许写 `environment/environment-record.json`。

## Input

- `python_version`：上游已经决定的 Python `major.minor` 或 `major.minor.patch`。
- `requirements[]`：上游已经决定的 Python 包要求；仅作为安装请求，不作为实际环境身份。
- `dependency_install_timeout_seconds`：可选。上游显式授权给单次依赖安装命令的硬超时，范围为 1–3600 秒；未提供时保持 240 秒默认值。
- `find_links[]`：可选的 HTTPS wheel/archive 来源，直接交给成熟包管理工具。
- `verify_imports[]`：环境完成后必须实际导入成功的关键 Python 模块。
- `runtime_readiness_probe`：可选、由上游明确给出的真实目标探针，包含目标环境内的 `executable`、参数化 `argv`、workspace 内 `working_directory` 和硬 `timeout_seconds`。

输入不负责决定论文、实验、checkpoint、数据集、依赖版本或应该探测哪个目标程序。`environment-setup` 不从 stderr 推断并安装未知依赖。

## Execution

1. 只允许在 Runtime 通过 `OPENAIR_ADAPTER_EXECUTION_CWD` 明确授权、且当前工作目录与其完全一致的 workspace 内执行。
2. 正式目标固定为 workspace 下的 `environment/`；若目标已存在或是符号链接，fail closed，不覆盖、不复用。
3. 优先复用 `uv` 按请求 Python 版本建立虚拟环境；若没有 `uv`，仅在当前 Python 已满足请求版本时回退到标准库 `venv`。
4. 通过目标环境自己的 `python -m pip` 一次安装上游给定依赖；只对这一次安装使用 `dependency_install_timeout_seconds`，未提供时为 240 秒，且始终受 3600 秒硬上限约束；不自研解析器、求解器或下载器。
5. 运行目标环境自己的 `pip check`。
6. 在目标环境内逐项实际导入 `verify_imports[]`。机器验证结果通过 workspace 内自动清理的独立临时 JSON 文件返回；被导入模块产生的 stdout / stderr 仍属于子进程输出，不参与机器结果解析。
7. 若配置 `runtime_readiness_probe`，使用目标环境内指定 executable 和参数化 argv 真实执行；不使用 shell；工作目录必须留在授权 workspace；执行有最长 300 秒硬超时，stdout/stderr 只保留有界证据。non-zero、Python module/import failure、timeout、executable 不存在或进程启动失败全部 fail closed。
8. 从目标环境实际读取：Python 实现与版本、平台事实、实际已安装包及版本。
9. 对这些实际环境事实做 canonical JSON + SHA256，形成 `fingerprint_sha256`。请求依赖、请求 Python 版本、验证模块、probe 命令及 stdout/stderr 不参与实际环境身份指纹。
10. 所有已配置验证通过后，原子写入正式环境记录。

## Output

成功时返回并持久化：

- `environment_identity.python`：实际 Python 实现和版本；
- `environment_identity.platform`：实际平台事实；
- `environment_identity.packages[]`：实际安装后的包名和版本；
- `environment_identity.fingerprint_sha256`：由上述实际事实计算的稳定指纹；
- `verification`：请求 Python 是否匹配、`pip check`、关键模块导入结果，以及 `runtime_readiness`；
- `verification.runtime_readiness`：未配置时明确为 `not_configured`；配置并通过时记录实际 executable、argv、工作目录、timeout、exit code 和有界 stdout/stderr；
- `execution_evidence`：实际使用的环境建立工具、pip 版本及关键操作退出码；其中依赖安装操作记录实际采用的 `timeout_seconds`；
- `workspace_relative_path = environment`；
- `environment_record_relative_path = environment/environment-record.json`。

失败时返回明确 `failure_code` / `failed_stage`。依赖安装失败诊断会记录实际采用的 `timeout_seconds`；超时仍返回 `dependency_install_timeout` / `dependency_installation`。真正模块导入失败返回 `import_verification_failed` 及逐模块结果；失败模块保留异常类型，以及经过限长和凭据脱敏的异常消息，不默认保存完整 traceback。只有独立结果文件缺失、不可读、JSON 非法、结构非法或退出状态不一致等协议异常才返回 `import_verification_invalid`，并保留有界且脱敏的 command、exit code、stdout/stderr tail 和协议错误原因。运行就绪失败会保留有界实际命令、exit code、stdout/stderr，并对标准 Python `ModuleNotFoundError` / `ImportError` 留下诊断类型和原始消息；不会自动安装任何包。若已产生部分环境则清理。清理本身失败必须显式报告 `environment_cleanup_failed`，不能继续声明成功。

## Responsibility Boundary

本 Skill **负责**：

- 已确定 Python/依赖要求的真实隔离环境建立；
- 必要依赖真实安装；
- `pip check` 与关键模块实际导入验证；
- 执行上游已经明确的低成本目标运行就绪探针；
- 从实际环境读取 Python、package、platform 事实；
- 实际环境身份固定与正式记录；
- 失败关闭和本轮部分环境清理。

本 Skill **不负责**：

- 决定复现哪个实验或应该探测哪个目标程序；
- Git 仓库、checkpoint 或数据获取；
- 静态计算完整 dependency closure；
- import graph / AST 全仓库依赖扫描；
- 依赖版本研究、自动升级、自动补包或冲突策略设计；
- CUDA、GPU 驱动、系统包或容器平台管理；
- 随机种子、实验参数、完整实验执行；
- 指标提取、结果比较；
- OpenAIR Binding / worker / durable runtime 正式集成；
- Artifact / provenance 平台接入；
- 论文或项目特判。

## Done

仅当同时满足以下条件才算成功：

1. `environment/` 由本轮真实创建；
2. 环境中的实际 Python 满足 `python_version`；
3. 全部 `requirements[]` 安装命令成功；
4. `pip check` 成功；
5. 全部 `verify_imports[]` 实际导入成功；
6. 若配置 runtime readiness probe，则 probe 使用最终环境真实 executable 执行且 exit code 为 0；
7. Python / platform / installed packages 均从实际环境读取；
8. `fingerprint_sha256` 仅由实际环境事实计算；
9. `environment-record.json` 只在全部已配置验证完成后出现；
10. 任一失败不会留下可被误认成正式成功环境的记录；
11. 实现没有论文/包名特判，也没有修改 OpenAIR 共享 Runtime。
12. 依赖安装实际使用上游授权预算或 240 秒默认值，并在成功执行证据或失败诊断中可追溯。

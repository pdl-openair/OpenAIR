---
name: mmp-training-workflow
description: >-
  指导在 mmp-platform 协同开发平台上执行训练流水线。当用户想要启动、配置或监控训练时使用
  — 无论是通过 Git+文件上传（快捷模式）、逐步选择资源、还是使用已有流水线模板。
  处理登录、资源查找、校验、启动、进度轮询和结果下载。
  **不是**用于资源增删改查（模型/代码/数据集）— 那些用 mmp-resources 技能。
  当用户提到"跑训练/训练任务/训练流水线/开跑/跑实验/远程训练/快捷训练"时应加载本技能。
---

# 协同开发平台远程训练流水线自动化

固定拓扑：**remote-task → model-export**。

## 前置条件

| 项 | 说明 |
|----|------|
| **MCP 服务** | 统一 **`mmp-platform`**，远程 HTTP，见 `mmp-unified/config/config.json` |
| 客户端注册 | `mmp-unified/.mcp.json`；OpenCode 见仓库根 `opencode.json` |
| 资源操作鉴权 | 优先 `login()` 无参（读 `mmp/.env` 的 `MMP_USERNAME`/`MMP_PASSWORD`）；`.env` 未配置时再向用户索取并 `login(username, password)` |
| 训练操作鉴权 | 同一 MCP；训练 run/validate 等由服务端 env 自动登录，Agent **无需**额外传 token |

## 核心交互原则

以下原则贯穿整个训练流程，理解了背后的原因之后自然知道什么时候该怎么做：

1. **先校验再开跑** — `validate_training_params` 返回 `ok=false` 时禁止调用 `run_training_pipeline`，避免无效任务浪费资源。
2. **导出的目标模型自动创建** — 训练结果需要保存，所以 `ensure_export_model` 自动创建导出目标，无需用户操心。**导出目标始终自动处理**，不要询问用户是否创建或索要名称。
3. **默认查个人资源，但提示公开库选项** — 用户一般先看自己的资源；如果个人库没有或用户想要公开资源，随时可切换。导出目标只限个人库。
4. **分阶段输出训练进度** — 长时间训练中用户需要感知进展，所以启动后立即反馈摘要，轮询推送日志和状态变化，最终给出总结（见「训练输出流程」）。
5. **禁止替用户做决定，除非模式 C 自动完成** — 模式 A/B 中，每个配置项由用户选择。模式 C 是快捷路径，自动化程度高，用户只需确认超参和选镜像。

## 模式自动判定（优先于一切提问）

用户已给出 Git + 本地文件时，不要询问「选 A/B/C」或「哪种训练方式」。

| 用户输入特征 | 判定模式 | Agent 动作 |
|-------------|----------|------------|
| **Git 仓库 URL** + **本地数据集文件路径**（`.zip`/`.tar`/目录等） | **C — 快捷训练** | 立即 `login()` → `prepare_quick_training(git_url, git_branch, dataset_file_path, model_file_path=…)`；**无须询问** |
| 同上，且附带本地模型/权重文件（`.ckpt`/`.pt`/`.pth`/`.onnx` 等） | **C**（含预训练） | `model_file_path` 传入 prepare；**无须询问**是否跳过预训练 |
| 「用已有流水线」「选模板」「跑实验」且未给 Git+本地文件 | **B** | `list_workflows` → 用户选名称 |
| 只给平台资源名/id，或明确要求「逐步选」「逐项配置」 | **A** | 逐项选配 |
| 仅说「训练」但无 Git、无本地文件、无平台资源名 | **才需要** | 简要说明 A/B/C，让用户选 |

模式 C 触发示例（看到类似消息直接走模式 C）：

```text
Git: https://openi.pcl.ac.cn/somunslotus/egnn.git  分支 master
数据集: C:\path\gnn_train_ori_data.zip
模型: C:\path\final_model.ckpt
```

## 三种训练方式概览

| 模式 | 适用场景 | Agent 动作 |
|------|---------|------------|
| **A — 逐项配置** | 新建 remote-task → model-export 流水线，逐步选择代码、数据集、镜像、模型等 | 按下文「逐项选配」；`validate_training_params` → 用户确认 → `run_training_pipeline` |
| **B — 已有流水线** | 使用平台上已保存的流水线模板（参数已在模板中配好） | `list_workflows` 分页展示 → 用户按名称选择 → `get_workflow_detail` 确认超参 → `create_and_run_experiment` |
| **C — 快捷训练** | Git + 本地文件；自动最大规格 + 自动创建数据集/模型 | `prepare_quick_training` → 超参全表一次确认 + 选镜像 |

### 模型参数对照

| 用户传入参数 | 注入 DAG 字段 | 节点 | 含义 |
|---------------|---------------|------|------|
| `export_model_id` / `export_model_name` | model-export **`--model_id`** | 导出 | 训练完成后权重导出到哪个模型仓库（**必填，自动创建**） |
| `model_id` / `model_name` + `model_version` | remote-task **`--model_name`** | 预训练 | 是否从已有模型版本加载初始权重（**可选**） |

注意：预训练用的是 **`model_id`（接口参数）→ `--model_name`（DAG 参数）**，不是 model-export 的 `--model_id`。这两个参数作用完全不同。

## 训练输出流程

用户确认参数并调用 `run_training_pipeline`（**默认 `wait=false`**）后，按三阶段向用户推送信息。这个推送节奏让用户感知训练进展，而不是静默等待后突然给出结果。

### 阶段 1 — 实验已启动（立即）

`run_training_pipeline` 返回 `launch_summary`，用如下格式回复：

```markdown
## 训练已启动

| 项 | 值 |
|----|-----|
| 流水线 | {workflow_name}（ID: {workflow_id}） |
| 实验 | {experiment_name}（ID: {experiment_id}） |
| 实例 | {argo_ins_name} |
| 状态 | {status} |
| 训练节点 | {remote_task_node_id} |
| 导出节点 | {model_export_node_id} |
| 输出目录 | {task_output_path} |
| 导出目标 | {export_model.name}（id: {export_model.id}） |
| 预训练模型 | {pretrain_model 或「不使用」} |
| 超参 | {global_param_text} |

正在监控训练进度，有状态或日志更新会继续通知。
```

保存供后续轮询：`experiment_id`、`remote_task_node_id`、`last_status`、`log_offset`（初始 0），以及 `launch_summary` 中的 **`export_model`**（含 id/name/owner/identifier/`latest_version_before_run`）与 **`task_output_path`**。

### 阶段 2 — 状态与日志更新（循环）

在实验未终态前，**每 30 秒**（或用户要求更频）调用 `poll_training_progress`，传入保存的 export 字段与 `baseline_export_version`。

每次调用后 **立即** 向用户输出（有内容才发，避免空刷屏）：

**状态变化时**（`status_update.changed=true`）：
```markdown
### 状态更新 {time}
- 实验状态：**{status}**
- 训练节点 phase：{remote_task_phase}
```

**有新日志时**（`log_delta` 非空）：
```markdown
### 训练日志（增量）
```text
{log_delta}
```
```

将返回的 `log_offset` 记为下次入参。`should_continue=false` 时进入阶段 3。

> **对话内「实时」**：靠 **多次调用** `poll_training_progress` 并分条发送，而非等 `wait_experiment` 一次返回。长训练勿用 `run_training_pipeline(wait=true)`，避免服务端超时。

**可选**：`watch_experiment` 在服务端批量轮询，适合一次性拉取时间线；日常仍优先 `poll_training_progress` 分次推送。

### 阶段 3 — 终态摘要

`should_continue=false` 时，响应含 **`completion_summary`**，用以下格式展示训练总结：

```markdown
## 训练完成
| 项 | 值 |
|----|-----|
| 状态 | {completion_summary.status_display} |
| 总耗时 | {completion_summary.duration 或「—」} |
| 产出文件 | {completion_summary.output_files_text 或「—」} |
| 输出目录 | {completion_summary.task_output_path} |
| 导出模型 | {completion_summary.export_model.display} |
```

- **`export_model.display`** 须含模型名称、id 与 **`saved_version`（本次保存的版本，如 v37）**
- 若 `saved_version` 为空但状态为成功，可说明「版本查询中或未返回，请到平台模型页确认」
- 可选展示 `saved_version_desc`（版本描述）

随后可：
1. 再调 `get_experiment_logs` 拉取完整尾部日志（`tail_chars=12000`）
2. 按需 `list_experiment_output_nodes` → `download_experiment_node_output` 下载产物

失败时 **不要** 擅自改参重跑，说明原因并请用户决定。

## 通用交互规范

这些规范适用于所有模式中的资源列表和用户交互。详细格式模板见 [reference.md](reference.md#列表展示格式)。

### 登录流程

1. 调用 **`login()`**（无参；服务端从 `mmp/.env` 读 `MMP_USERNAME`/`MMP_PASSWORD`）
2. 若返回 `missing_credentials`，再向用户收集 `username`、`password`，调用 `login(username, password)`
3. 从返回 JSON 取 `data.access_token` 作为 `token`，供所有资源 query 调用
4. 训练类工具（validate/run/poll）直接调用，无需传 token

### 资源查询 → 训练参数映射

| 用户选定资源 | 从 query 响应提取 | 传入 validate / run 的参数 |
|-------------|-------------------|---------------------------|
| 代码配置 | `content[].id`, `code_repo_name` | `code_config_id` 或 `code_repo_name` |
| 数据集 | `id`, `name`, `identifier`, `owner`/`create_by` | `dataset_id` 或 `dataset_name` |
| 数据集版本 | `dataset_get_version_list` 返回的版本名 | `dataset_version` |
| 导出模型 | `model_query` 中的 `id`, `name` | `export_model_id` 或 `export_model_name` |
| 预训练模型 | 同上 | `model_id` 或 `model_name` + `model_version` |

列表在 `data.content`（或顶层 `content`）中。

### 资源范围（个人与公开）

训练选配中的**代码配置、数据集、预训练模型**支持个人库与平台公开库；**导出目标模型**仅个人库。

**开场说明**（进入「逐项选配」后、步骤 1 之前，对用户说明）：

> 接下来选择代码配置、数据集和（若需要）预训练模型。**默认先展示您账号下的个人资源**。
> 若需要使用平台**公开**的代码配置、数据集或预训练模型，请直接说「查公开库」或「也要看公开的 xxx」。我会在当前步骤用公开库再查一次。
> **训练结果的导出目标**只能导出到您个人的模型仓库，不能选公开库。

**每一步的标准流程**（代码配置/数据集/预训练模型三类）：

1. **先查个人库**：调用 query 工具，**不传** `is_public` 或传 `is_public=false`
2. **展示列表**，标题标明「**个人库**」，供用户选择
3. **必须追加提示**：告知用户可查看公开库
4. **仅当用户明确要求公开库时**，再调用同一 query 工具并传 **`is_public=true`**，展示「**公开库**」列表
5. 用户在个人或公开列表中**选定一项**后，再进入下一步

**特殊情况处理**：

| 情况 | Agent 动作 |
|------|------------|
| 个人库列表为空 | 告知无个人资源，**主动询问**是否查公开库 |
| 用户说「公开/平台/别人的」 | 当前步骤用 `is_public=true` 查询 |
| 用户已从公开库选中 | 后续 `dataset_get_version_list` / `model_get_version_list` 使用选中项的 `owner`、`identifier` |
| **导出目标模型** | **只查个人库**，不询问、不提供公开库；说明「导出只能到您的个人模型仓库」 |

**`is_public` 传参约定**：不传或 `false` 为个人资源，`true` 为平台公开资源。不要跳过个人库直接查公开库。

### 列表展示要求

向用户展示查询结果时，**不要**只报 id/名称或一次性输出整页 JSON。具体表格格式见 [reference.md#列表展示格式](reference.md#列表展示格式)，这里只说明通用原则：

- **分页**：固定 `size=20`，首次 `page=0`；用户说「下一页」时 `page += 1`，须等用户确认后再翻页。条目数 `< 20` 表示已到末页。
- **关键字搜索**：资源总数 >20 或用户说「太多/找不到」时，提示用户提供名称关键字缩小范围，**不要**增大 `size` 或连续自动翻页。
- **数据集版本 / 模型版本**：调用 `dataset_get_version_list` / `model_get_version_list` 后，**必须**展示版本描述。
- **资源规格**：`list_resource_specs` 返回的每条须列出硬件明细（CPU/内存/显存/存储），**不可只写名称**。
- **Git 地址格式**：展示为 `{url} ({branch})`，不要写成 Markdown 链接 `[url](url)`，不要单独开「分支」列。

## 模式 A — 逐项选配

用户未一次性给出全部配置时，按下列顺序调用 MCP；**每条由用户选定**后再进入下一步。

| 步骤 | 用户选择 | 调用工具 |
|------|----------|------|
| 1 | 代码配置 | `code_query(token, page, size=20, …)` |
| 2 | 数据集 | `dataset_query(token, page, size=20, name=…, …)` |
| 3 | 数据集版本 | `dataset_get_version_list(token, owner, identifier)` |
| 4 | 资源类型 | `list_resource_types` |
| 5 | 资源规格 | `list_resource_specs(resource_type=…)` |
| 6 | 训练镜像 | `list_training_images(resource_type=…)` |
| 7 | 启动文件 | 用户填写**相对路径文件名**，如 `train.py`；**不要** `python train.py` / `bash run.sh` |
| **8** | **导出目标** | **`ensure_export_model(code_repo_name=…)`** — **自动创建**，命名 `{code_repo}-export-{时间}`；**无须询问用户** |
| **8.3～8.5** | 预训练（可选） | 见下文 |
| 9 | 输出目录 | 用户填写，如 `/result` |
| 10 | 超参 | 若已 `analyze_training_repo`：展示 **hyperparams_catalog**（仅训练超参）；否则用户 JSON |

然后：`validate_training_params`（含 `skip_pretrain_model` 或预训练模型信息）→ 参数确认表 → 用户确认 → `run_training_pipeline(wait=false)`。

**步骤 7 提问用语**：「请提供**启动文件**，如：`train.py`」— 只收**相对路径**，传入 `command` 时与 `analyze_training_repo.entry_command` 一致。

### 步骤 8：模型配置

remote-task 与 model-export 使用**不同**模型参数：

| 类型 | DAG 参数 | 是否必填 | 说明 |
|------|----------|----------|------|
| **导出目标** | model-export `--model_id` | **必填（自动）** | 训练**默认保存模型**；调用 `ensure_export_model` 或 `run_training_pipeline` 自动创建。**不要询问用户是否创建或索要名称** |
| **预训练/加载** | remote-task `--model_name` | **可选** | 是否从已有模型版本加载初始权重，**须先问用户是否使用** |

**导出目标（8.1）**：始终需要，自动处理。调用 `ensure_export_model(code_repo_name=当前代码仓库名)`，在确认表/启动摘要中展示 `导出目标：{name}（id: {id}，已自动创建）`。

**预训练模型（8.2）**：先问「本次训练是否需要加载预训练模型（remote-task 的 `--model_name`）？」：
- **不需要** → `skip_pretrain_model=true`
- **需要** → 按资源范围标准流程选预训练模型及版本（步骤 8.3～8.5）：`model_query` → 分页展示（个人 → 公开）→ `model_get_version_list`

## 模式 B — 已有流水线

用户选择模式 B 时，**不要**询问或要求用户提供 `workflow_id`（用户不知道 id）。

### 1. 分页列出流水线

- 调用 `list_workflows(page=0, size=10)`（**固定每页 10 条**）
- 展示表格，列：**# | id | 名称 | 描述 | 创建人 | 更新时间 | 默认超参预览**
- 若 `total_elements > 10` 或 `has_next=true`，提示可翻页或按名称关键字搜索
- 用户说「下一页」再翻页，**不要**连续自动翻页

### 2. 用户选择流水线

用户按**序号**或**名称**选定一条，Agent 内部记录对应的 `id`，**不要**让用户复制 id。

### 3. 确认超参与实验名

- `get_workflow_detail(workflow_id)` 展示 `global_param` 默认值
- 询问实验名称（可空则自动生成）
- 询问是否覆盖部分超参；仅变更项写入 `global_params_json`，未提及的键沿用模板默认值

### 4. 创建并运行

`create_and_run_experiment(workflow_id=…, experiment_name=…, global_params_json=…)` → 展示启动摘要后进入阶段 3 `poll_training_progress`。

**禁止**：索要 `workflow_id`、用 `run_training_pipeline` 新建流水线、未展示列表即创建。

## 模式 C — 快捷训练（Git + 文件上传）

> **核心交互**：用户**仅**在一轮内确认超参（全表一次给出）并选择**训练镜像**。其余全部自动。

### 用户与系统自动分工

| 类别 | 内容 | 谁提供 |
|------|------|--------|
| **用户必填** | Git 地址 + 分支、数据集文件路径 | 用户 |
| **用户可选** | 预训练模型文件路径 | 用户 |
| **用户必选** | **训练镜像** `image_name` | 用户 |
| **用户一次性确认** | **全部超参**（`hyperparams_table` 一次展示；用户说改哪些、必填项须给值） | 用户 |
| **自动** | 资源类型推断（代码 GPU/NPU 信号）+ **该类型下最大资源规格** | `prepare_quick_training` |
| **自动** | 数据集/模型类型推断、上传创建 | 推断后直接创建，**无须用户确认** |
| **自动** | 入口、输出路径、export_model、有默认值超参 | `prepare_quick_training` |

### 流程

```
login → prepare_quick_training(...)
  → draft 含 resource_type + resource_spec_name（该类型最大规格）

→ list_training_images(resource_type=draft.resource_type)   # 不要 list_resource_types / list_resource_specs

→ **一条消息**：
   - 已自动项摘要（含 resource_type、最大 resource_spec、推断原因）
   - hyperparams_table **全表** + hyperparams_user_prompt + hyperparams_risk
   - 镜像列表

→ 用户一次回复：镜像 +（可选）超参修改 + 必填超参

→ validate(mode=quick, **draft, image_name=…) → run_training_pipeline
```

### 单条消息模板

```markdown
## 快捷训练 — 请确认超参并选择镜像

**已自动配置（无须再确认）：**
| 项 | 值 |
|----|-----|
| 资源类型 | {resource_type}（{inference_reason}） |
| 资源规格 | {resource_spec_name}（**该类型下已选最大**；{display_summary}） |
| 数据集 | {name} · {data_type}/{data_tag}（推断置信度 {confidence}，已创建） |
| 导出目标 | {export_model.name} |
| 启动文件 / 输出目录 | {command} / {task_output_path} |

### 训练超参（一次性全部列出）

> **不含**平台参数 `--model_name` / `--dataset` / `--task_output`（已由平台注入，见上表）。

{hyperparams_user_prompt}

> {hyperparams_risk}

| 平台键 | argparse | 当前值/默认 | 是否必填 |
|--------|----------|-------------|----------|
| bs | batch_size | 32 | 否 |
| lr | learning_rate | — | **是（未填可能失败）** |

请回复：镜像名称 + 需修改的超参（如 `lr=0.001`）+ 必填项取值。

### 训练镜像（必选）
| # | 名称 | imageID |
```

### 模式 C 需注意的要点

模式 C 的目的是省去用户逐项配置的负担，所以大部分事情自动完成。以下行为会破坏这种体验：

- 调用 `list_resource_types` / `list_resource_specs` 让用户选择或确认（直接用 `draft.resource_type` + `draft.resource_spec_name`）
- 询问或让用户确认 `resource_type` / `resource_spec_name` / `data_type` / `data_tag` / `model_type` / `model_tag`
- 分步、逐项询问超参（须 `hyperparams_table` **一次给全**）
- 使用分步表单方式询问
- 无 `model_file_path` 仍询问预训练（须自动 `skip_pretrain_model=true`）
- 让用户选 export_model 或填写 version_desc
- 替用户默认选镜像（须展示列表后用户选）
- 在无默认值超参未补齐时开跑
- 跳过 `prepare_quick_training` 自行拆步 analyze → 逐步确认 → provision

### 模式 C 专用 MCP

| 工具 | 说明 |
|------|------|
| **`prepare_quick_training`** | **首选**；draft 含推断的资源类型 + 最大资源规格，数据集/模型已创建 |
| `list_training_images` | 展示镜像供用户选 |
| `validate_training_params(mode=quick, user_confirmed=true)` | 用户确认超参 + 选定镜像后调用 |
| `run_training_pipeline` | 传入 draft 字段 + `image_name` |

`analyze_training_repo` / `provision_training_assets` 等由 `prepare_quick_training` 内部调用，Agent **不要**拆步重复问用户。

## 实验产物下载（终态后）

当用户需要下载流水线节点输出（如 `/result` 下的模型与日志）时，在实验 **成功结束** 或用户明确要求后：

1. `list_experiment_output_nodes(experiment_id)` — 默认**仅 remote-task**（`--task_output`、`/remote-logs`），不含 model-export 导出目录；需全节点时 `remote_task_only=false`。
2. `get_experiment_node_outputs(experiment_id, node_id)` — 获取目录内文件列表。
3. `download_experiment_node_output(storage_path=..., save_dir=...)` — 下载 zip 到本地。

**说明**：`storage_path` 须为平台返回的完整 MinIO 路径；下载为 zip 包。向用户展示列表后再下载，避免误下大文件。

## MCP 工具速查

### 资源查询

| 操作 | 工具 |
|------|------|
| 登录 | `login` |
| 列代码配置 | `code_query(token, …)` |
| 列数据集 | `dataset_query(token, …)` |
| 列数据集版本 | `dataset_get_version_list(token, owner, identifier)` |
| 列模型 | `model_query(token, is_public=…)` |
| 列模型版本 | `model_get_version_list(token, owner, identifier)` |

### 训练专用

| 阶段 | 工具 |
|------|------|
| 列资源类型 / 规格 / 训练镜像 | `list_resource_types`、`list_resource_specs`、`list_training_images` |
| 快捷训练（模式 C） | **`prepare_quick_training`**、`ensure_export_model`、`list_training_images` |
| 导出目标（模式 A） | **`ensure_export_model`**（自动创建，无须询问用户） |
| 校验 | `validate_training_params`（`mode=quick` + `user_confirmed=true` 用于模式 C） |
| 解析名称 | `resolve_training_assets` |
| 列流水线（模式 B） | `list_workflows`（每页 10 条）→ `get_workflow_detail` |
| 发起训练（模式 A） | `run_training_pipeline`（**wait=false**） |
| 发起实验（模式 B） | `create_and_run_experiment`（workflow_id 来自用户选择） |
| 进度 / 日志 | `poll_training_progress` |
| 终态日志 | `get_experiment_logs` |
| 节点产物 | `list_experiment_output_nodes` / `get_experiment_node_outputs` / `download_experiment_node_output` |

### 快捷流程（用户已给全名，模式 A/B）

```
提取参数 → validate_training_params
  → 缺项：调用 mmp-platform 的 query / list_* 工具 → 停止
  → 参数确认表 → 用户确认 → run_training_pipeline(wait=false)
  → 阶段 1 → 循环阶段 2 → 阶段 3
```

## 相关文档

- [mmp-resources 技能](../mmp-resources/SKILL.md)（资源的增删改查详述）
- [reference.md](reference.md)（API 速查、展示格式模板、配置文件说明）

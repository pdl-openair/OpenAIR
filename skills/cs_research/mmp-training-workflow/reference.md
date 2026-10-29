# 协同开发平台训练流水线 API 速查

详细样例见项目根目录 `流水线和实验接口.md`、`实验列表.txt`。

## 基址

| 环境 | 基址 |
|------|------|
| 协同开发平台 | `https://www.ai4mats.com` |
| 资源/镜像 JSM | `https://ai4m.jointcloud.net` |

## 鉴权

| 基址 | 环境变量 | 说明 |
|------|----------|------|
| 协同开发平台 | `MMP_USERNAME` / `MMP_PASSWORD`（`mcp/.env`） | Agent 调用 **`login()` 无参** 自动使用；未配置再向用户索取 |
| JSM | `JSM_USERNAME`、`JSM_PASSWORD` | MCP 自动调用 `POST /jcc-admin/admin/login` 获取 Token |

勿将 Token/密码写入技能文件或提交到 Git。

## 流水线

| 操作 | 方法 | 路径 |
|------|------|------|
| 创建 | POST | `/api/mmp/workflow/create` |
| 保存 | PUT | `/api/mmp/workflow` |
| 列表 | GET | `/api/mmp/workflow?page=0&size=10`（可选 `name` 关键字） |
| 详情 | GET | `/api/mmp/workflow/{id}` |
| 组件列表 | GET | `/api/mmp/component/components/all` |

## 资源查询（统一 mmp-platform MCP）

**代码 / 数据集 / 模型** → `code_query` / `dataset_query` / `model_query`（须先 `login`）

**JSM 资源规格与训练镜像** → `list_resource_*` / `list_training_images`

### 代码分析与超参（`analyze_training_repo` / `prepare_quick_training`）

| 字段 | 说明 |
|------|------|
| `hyperparams_catalog` | 训练超参列表（**不含**平台参数） |
| `platform_pipeline_args` | 平台注入参数：`--model_name` / `--dataset` / `--task_output` |
| `excluded_args` | 全部非超参（platform / runtime / path_io / other） |
| `args_catalog` | 入口脚本 argparse 全量参数及分类 |
| `has_default` / `default_value` | 代码中有 argparse 默认值 |
| `user_must_provide` | 无默认值，须用户填写 |
| `global_params` | 仅有默认值项，可自动传入 `global_params_json` |
| `resource_type_inference` | 代码信号推断 GPU/NPU（含 signals、reason） |
| `auto_resource` | prepare_quick_training 返回的已自动资源（type + 最大 spec） |
| `auto_selected_spec` / `largest_spec` | 自动选最大资源规格（显存>内存>CPU） |
| `hyperparams_table` | 超参全表（一次展示给用户） |
| `hyperparams_user_prompt` | 一次性确认话术 |
| `params_requiring_user_input` | 须用户提供的平台键列表 |
| `hyperparams_risk` | 无默认值项未填可能导致启动失败的风险说明 |
| `auto_inferred` | 数据集/模型类型由推断器自动采用，勿问用户 |

| 操作 | 工具 |
|------|------|
| 代码配置 | `code_query` |
| 数据集 | `dataset_query` |
| 数据集版本 | `dataset_get_version_list` |
| 模型 | `model_query` |
| 模型版本 | `model_get_version_list` |
| 资源规格 | `list_resource_specs` → 含 cpu/memory/vram/storage |
| 训练镜像 | `list_training_images` |
| 自动导出目标 | **`ensure_export_model`**（默认创建，勿问用户） |

## 实验

| 操作 | 方法 | 路径 |
|------|------|------|
| 创建 | POST | `/api/mmp/experiment` |
| 运行 | PUT | `/api/mmp/experiment/experiments/{id}` |
| 列表 | GET | `/api/mmp/experiment?page=0&size=10` |
| 实例/状态 | GET | `/api/mmp/experimentIns?experimentId={id}&page=0&size=5` |
| 实时日志 | POST | `/api/mmp/experimentIns/realTimeLog/` |
| Pod 日志 | GET | `/api/mmp/experimentIns/pods/log?pod_name=&start_time=` |

## 实验创建 body 注意

`global_param` 字段类型为 **string**，内容为 JSON 数组字符串，例如：

```json
"global_param": "[{\"param_name\":\"bs\",\"param_value\":\"8\",\"param_type\":2,\"description\":\"bs\"}]"
```

## 节点引用语法

上游 remote-task 输出供 model-export 使用：

```
{{remote-task-{nodeSuffix}.--task_output}}
```

`nodeSuffix` 与节点 `id` 中 `-` 后的 8 位 hex 一致。

## 运行后上下文变量

| 变量 | 来源 |
|------|------|
| `workflow_id` | create / save 响应 |
| `experiment_id` | POST experiment |
| `experiment_ins_id` | PUT run → `experiment_ins_list[0].id` |
| `argo_ins_name` | PUT run → `experiment_ins_list[0].argo_ins_name` |
| `remote_task_node_id` | DAG 节点 id，如 `remote-task-ff3e16fb` |

日志请求中的 `task_id` = `remote_task_node_id`。

---

## 列表展示格式

在 SKILL.md「通用交互规范」基础上，以下给出每种资源的具体表格格式。遵循这些格式能让用户快速浏览和选择，避免信息混乱。

### 代码配置

- 调用：`code_query(token, page=0, size=20, is_public=…)`
- **每条必须展示**：`id`、`code_repo_name`（名称）、`git_url` + `git_branch`（合并为一列）
- **Git 地址列格式**：`{git_url} ({git_branch})`，例如 `https://openi.pcl.ac.cn/somunslotus/egnn.git (master)`
- 推荐表格（**三列**：id、名称、Git 地址）：

```markdown
### 代码配置（个人库 · 第 1 页）

| # | id | 名称 | Git 地址 |
|---|-----|------|----------|
| 1 | 28 | atom-train | https://openi.pcl.ac.cn/somunslotus/egnn.git (master) |
| 2 | 21 | 数据质量分析 | https://www.gitlink.ai4mats.com/your-gitlab-user/material_quality_service.git (master) |
```

- 若响应含 `totalElements` / `total` / `totalPages`，一并告知；本页满 20 条时提示查看更多

### 数据集 / 模型

- **固定 `size=20`**，**禁止** `size=500/2000` 一次拉全量
- 首次 `page=0`；用户说「下一页/更多」时 `page += 1`，**须等用户确认后再翻页**
- 数据集展示：`id`、`name`、`identifier`、`owner`/`create_by`、`data_type`（若有）
- 模型展示：`id`、`name`、`model_type`、`model_tag`、`owner`/`create_by`（若有）
- 页眉示例：`### 数据集（个人库 · 第 2 页，共 45 条）`
- 本页条目数 `< 20` 表示已到末页

**关键字搜索提示**（满足任一条件即展示）：

- 响应 `totalElements` / `total` **> 20**
- 本页满 **20** 条且仍有下一页
- 用户说「太多 / 找不到 / 有没有 xxx」

提示语示例：

> 当前共有 **N** 条{数据集|模型}，本页仅展示第 **M** 页。您可直接说 **名称关键字**（如 `cif`、`bert`）缩小范围，或说 **下一页** 继续浏览。

用户给出关键字后，在查询中传入 `name=关键字`，保持当前个人/公开范围。关键字搜索后仍满 20 条：继续提示可换更精确关键字或翻页。无匹配结果：告知用户，建议换关键字、查公开库，或确认是否已创建。

### 导出目标模型

- 同样 **`size=20` 分页**（仅个人库）
- 展示：`id`、`name`；无需 git 字段

### 资源规格

`list_resource_specs` 返回的每条含 **`cpu`、`memory`、`vram`（显存）、`storage`** 与 **`display_summary`**：

```markdown
### 资源规格（GPU）

| # | 名称 | 可用/总量 | CPU | 内存 | 显存 | 存储 |
|---|------|-----------|-----|------|------|------|
| 1 | A100 | 4/4 | 96 core | 256 GB | 32 GB | 1024 GB |
```

- 内存/显存/存储单位展示为 **`GB`**（大写），勿用 `gb`
- 若某字段为空，显示「—」
- 可直接引用 API 返回的 `display_summary` 作为补充说明

### 数据集 / 模型版本列表

调用 `dataset_get_version_list` / `model_get_version_list` 后，**必须**展示版本描述：

```markdown
### 数据集版本（{dataset_name}）

| # | 版本 | 版本描述 |
|---|------|----------|
| 1 | v4 | 2026-05 全量更新 |
| 2 | v3 | — |
```

- 优先读响应 `version_details[].version_desc`（工具已附加）；或从 `data[]` 中读 `version_desc` / `versionDesc`
- 无 `version_desc` 时显示「—」

### 流水线列表（模式 B）

`list_workflows(page=0, size=10)`，展示：

```markdown
### 流水线（第 1 页，共 N 条）

| # | id | 名称 | 描述 | 创建人 | 更新时间 | 默认超参预览 |
|---|-----|------|------|--------|----------|-------------|
| 1 | 42 | my-training | 训练... | user1 | 2026-06-01 | lr=0.001, bs=32 |
```

- 固定每页 10 条，`has_next=true` 时提示翻页或关键字搜索

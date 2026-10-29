---
name: mmp-resources
description: >-
  Manages collaborative development platform resources: models, code repos, images, and datasets
  (create, query, update, delete, version upload, publish, download).
  Use when user mentions 模型、代码配置、镜像、数据集、创建、查询、更新、删除、版本、上传.
  NOT for starting training runs — use skill mmp-training-workflow instead.
---

# 协同开发平台资源管理（统一 Skill）

本 Skill 管理协同开发平台上的四类资源。**所有操作必须通过 MCP 服务 `mmp-platform` 调用工具**，禁止直接运行 `scripts/` 或手写 HTTP 请求。

> **训练开跑**（Git + 本地文件上传训练、跑流水线、prepare_quick_training）→ 使用 **`mmp-training-workflow`** Skill，**不要**在本 Skill 里询问训练方式 A/B/C。

## 前置条件

- MCP 服务：**`mmp-platform`**（远程 HTTP，URL 见 `config/config.json` 的 `MCP_PUBLIC_URL`，默认 `http://127.0.0.1:8000/mcp`）
- 客户端注册：见 `mmp-unified/.mcp.json` 或仓库根 `opencode.json`
- 协同开发平台 API：`config/config.json` 中的 `API_BASE_URL`
- 平台凭证：`mmp-unified/mcp/.env` 中 `MMP_USERNAME` / `MMP_PASSWORD`（已配置则 Agent **勿**向用户索要）；未配置时再询问用户

## 通用前置步骤

1. 调用 `login()`（**优先无参**；服务端从 `.env` 读取账号）；若返回 `missing_credentials` 再向用户收集 `username`、`password` 并 `login(username, password)`
2. 解析返回 JSON：`code == 200` 时取 `data.access_token` 作为 `token`
3. 后续所有工具调用均传入 `token`
4. Token 过期则重新登录

## 资源范围（个人 / 公开）

查询代码配置、模型、数据集、镜像时，**默认先查个人库**；Agent 须让用户知道也可查公开库。

### 默认与提示

| 场景 | 行为 |
|------|------|
| 用户未说明 public/公开 | 先 `*_query(..., is_public=false)` 或不传 `is_public` |
| 展示个人列表后 | **必须提示**：「以上是您的个人资源。如需平台公开资源，请说查公开库。」 |
| 用户要求公开/平台 | 再 `*_query(..., is_public=true)` |
| 个人列表为空 | 主动询问是否查公开库 |

### 各资源 query 工具

| 资源 | 个人库 | 公开库 |
|------|--------|--------|
| 代码 | `code_query(token, is_public=false, …)` | `code_query(token, is_public=true, …)` |
| 模型 | `model_query(token, is_public=false, …)` | `model_query(token, is_public=true, …)` |
| 数据集 | `dataset_query(token, is_public=false, …)` | `dataset_query(token, is_public=true, …)` |
| 镜像 | `image_query(token, is_public=false, …)` | `image_query(token, is_public=true, …)` |

详情/版本/下载接口若含 `is_public` 参数，与所选资源 scope 保持一致。

## 列表展示规范

### 代码配置

展示 `code_query` 结果时须含 `git_url` 与 `git_branch`，**合并为一列**：

- 表格列：**id**、**名称**（`code_repo_name`）、**Git 地址**
- Git 地址格式：`{git_url} ({git_branch})`，如 `https://.../repo.git (master)`
- **禁止** `[url](url)`  Markdown 链接（会导致地址显示两次）
- **禁止**单独「分支」列

### 数据集 / 模型

- **固定 `size=20`**，按 `page=0,1,2…` 分批展示
- **`totalElements` / `total` > 20**，或本页满 20 条且非末页时，**必须提示**用户可说**名称关键字**缩小范围，或说「下一页」翻页
- 用户给出关键字后：`model_query(..., name=关键字, page=0)` 或 `dataset_query(..., name=关键字, page=0)`（保持当前个人/公开 scope）
- 禁止一次 `size=500/2000` 拉全量；禁止为找某一项而连续自动翻页

### 资源规格（训练选配）

`list_resource_specs` 返回 `cpu`、`memory`、`vram`（显存）、`storage`、`display_summary`；展示时须列出硬件明细，不可只报名称。内存/显存/存储单位用 **`GB`**（大写）。

## 意图路由（第一步必须判断）

| 用户意图 | 资源类型 | 场景 |
|---------|---------|------|
| 创建/新建 | 模型 | [模型-创建](#模型-创建) |
| 查询/列表/详情 | 模型 | [模型-查询](#模型-查询) |
| 更新/修改 | 模型 | [模型-更新](#模型-更新) |
| 删除 | 模型 | [模型-删除](#模型-删除) |
| 上传版本 | 模型 | [模型-创建版本](#模型-创建版本) |
| 创建/新建 | 代码配置 | [代码-创建](#代码-创建) |
| 查询/列表 | 代码配置 | [代码-查询](#代码-查询) |
| 更新/修改 | 代码配置 | [代码-更新](#代码-更新) |
| 删除 | 代码配置 | [代码-删除](#代码-删除) |
| 创建/新建 | 镜像 | [镜像-创建](#镜像-创建) |
| 查询/列表 | 镜像 | [镜像-查询](#镜像-查询) |
| 删除 | 镜像 | [镜像-删除](#镜像-删除) |
| 创建/新建 | 数据集 | [数据集-创建](#数据集-创建) |
| 查询/列表/详情/发布 | 数据集 | [数据集-查询](#数据集-查询) |
| 更新/修改简介 | 数据集 | [数据集-更新](#数据集-更新) |
| 删除/删版本 | 数据集 | [数据集-删除](#数据集-删除) |
| 上传版本 | 数据集 | [数据集-创建版本](#数据集-创建版本) |
| 下载 | 数据集 | [数据集-下载](#数据集-下载) |

完整工具列表见 [reference.md](reference.md)。

---

## 模型

### 模型-创建

1. `model_query_type_list(token)` → 从 `data[].second_asset_icon_list[].name` 获取可选类型
2. 向用户确认 `model_type`、`model_tag`（必须在类型列表内）
3. `model_create(token, name, model_tag, model_type)`
4. 成功时展示 `data.name`，并询问是否创建版本

### 模型-创建版本

1. 从创建结果或用户输入获取：`git_id`, `id`, `identifier`, `name`, `owner`（即 username）
2. 向用户收集 `version_desc` 和本地 `file_path`
3. `upload_file(token, file_path)` → 取返回 JSON 的 `location` 作为 `file_location`
4. `model_query_next_version(token, identifier, owner)` → 取版本号
5. `model_create_version(token, git_id, id, identifier, file_path, file_location, name, owner, version, version_desc)`

### 模型-查询

1. 默认 `model_query(token, page, size, is_public=false, ...)`，展示**个人库**并提示可查公开库
2. 用户要求时 `model_query(..., is_public=true)`，展示**公开库**
3. 资源较多（total>20 或本页满 20 条）：**必须提示**用户可说名称关键字，再用 `model_query(..., name=关键字, page=0)` 过滤
4. 详情：`model_get_detail(token, git_id, identifier, owner, is_public)`（`is_public` 与所选资源一致）
5. 支持分页：用户要求下一页时 `page += 1`

### 模型-更新

1. `model_query(token, ...)` 列出候选
2. 用户选择后确认新 `model_type`（可先 `model_query_type_list`）、`model_tag`
3. `model_update(token, id, git_id, identifier, owner, model_type, model_tag)`

### 模型-删除

1. `model_query(token, ...)` 列出候选
2. 用户选择后 `model_delete(token, id)`

---

## 代码配置

### 代码-创建

`code_create(token, code_repo_name, git_url, git_branch, is_public)`

### 代码-查询

1. 默认 `code_query(token, page, size, is_public=false, ...)`，展示个人库并提示可查公开库
2. 用户要求时 `code_query(..., is_public=true)`

### 代码-更新

1. `code_query(token, ...)` 列出候选
2. 用户选择并确认修改字段
3. `code_update(token, id, code_repo_name, git_branch, git_url, is_public)`

### 代码-删除

1. `code_query(token, ...)` 列出候选
2. `code_delete(token, id)`

---

## 镜像

### 镜像-创建

`image_create(token, name, tag_name, description, version_description, path)`

### 镜像-查询

1. 默认 `image_query(token, page, size, is_public=false, name=...)`，展示个人库并提示可查公开库
2. 用户要求时 `image_query(..., is_public=true)`

### 镜像-删除

1. `image_query(token, ...)` 列出候选
2. `image_delete(token, id)`

---

## 数据集

### 数据集-创建

#### 简化流程（推荐）
直接使用一站式工具，一步完成数据集创建和版本上传：

1. `dataset_create_with_version(token, name, data_tag, file_path, data_type="通用数据", version_desc="初始版本")`
2. 成功时返回数据集信息和版本信息

#### 完整流程（可选）
如需精细控制，可使用分步流程：

1. `dataset_get_parent_categories(token)` → 获取父级分类列表
2. 向用户展示父级分类，询问需要展开哪个分类
3. `dataset_get_child_categories(token, parent_id)` → 获取该父级下的叶子节点列表
4. 向用户展示叶子节点，确认 `data_type`（必须在叶子节点列表内）
5. `dataset_create(token, name, data_tag, data_type)`
6. 成功时询问是否创建版本

### 数据集-创建版本

1. 从创建结果获取：`git_id`, `id`, `identifier`, `name`, `owner`
2. `upload_file(token, file_path)` → `file_location`
3. `dataset_query_next_version(token, identifier, owner)` → `version`
4. `dataset_create_version(token, git_id, id, identifier, file_path, file_location, name, owner, version, version_desc)`

### 数据集-查询

1. 默认 `dataset_query(token, page, size, is_public=false, ...)`，展示个人库并提示可查公开库
2. 用户要求时 `dataset_query(..., is_public=true)`
3. 资源较多（total>20 或本页满 20 条）：**必须提示**用户可说名称关键字，再用 `dataset_query(..., name=关键字, page=0)` 过滤
4. 详情：`dataset_get_detail(token, git_id, owner, name, identifier, is_public)`
5. 版本列表：`dataset_get_version_list(token, owner, identifier)` → 表格展示 **版本名 + version_desc（版本描述）**；无描述显示「—」
6. 发布：`dataset_publish(token, id, name)`（仅个人数据集）

### 数据集-更新

- 元数据：`dataset_update(token, id, name, identifier, ...)`（字段来自详情查询结果）
- 仅简介：`dataset_update_desc(token, git_id, identifier, description)`

### 数据集-删除

- 删版本：`dataset_delete_version(token, git_id, owner, identifier, relative_paths, version)`
- 删整个：`dataset_delete(token, id)`

### 数据集-下载

- 全部：`dataset_download_all(token, name, git_id, version, identifier, owner, is_public)`
- 单文件：`dataset_download_single(token, url)`

---

## 错误处理

- 登录失败 → 终止，提示检查用户名密码
- `code != 200` → 展示 `msg`，区分登录失败 vs 业务失败
- 上传/下载超时 → 提示重试
- 不要将 username、password、token 写入日志

## 示例

**用户：** 帮我查一下私有模型列表

**Agent：**
1. `login` → 获取 token
2. `model_query(token, is_public=false)` → 展示**个人库**列表
3. 提示：「如需查看平台公开模型，请说查公开库。」
4. 询问是否查看详情或翻页

**用户：** 创建数据集 test-data，标签 ml，并上传 v1 版本

**Agent：**
1. `login` → token
2. `dataset_create` → 保存 git_id/id/identifier
3. `upload_file` → file_location
4. `dataset_query_next_version` → version
5. `dataset_create_version` → 反馈结果

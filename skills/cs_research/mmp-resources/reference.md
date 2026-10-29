# 协同开发平台资源 MCP 工具参考

MCP 服务名：`mmp-platform`

## 资源范围（is_public）

| 值 | 含义 | 使用时机 |
|----|------|----------|
| 不传 / `false` | 个人资源 | **默认第一步** |
| `true` | 平台公开资源 | 用户明确要求，或个人库无合适项且用户同意 |

Agent 展示个人列表后应提示用户可查询公开库（训练选配见 `mmp-training-workflow` Skill）。

## 训练资源规格（list_resource_specs 返回字段）

| 字段 | 说明 |
|------|------|
| `name` | 规格名称（如 A100、ASCEND910） |
| `cpu` | CPU 核数，如 `96 core` |
| `memory` | 内存，如 `256 GB` |
| `vram` | 显存，如 `32 GB` |
| `storage` | 存储，如 `1024 GB` |
| `availableCount` / `totalCount` | 可用/总量 |
| `display_summary` | 一行可读摘要 |

## 通用

| 工具 | 说明 |
|------|------|
| `login(username="", password="")` | 登录；**参数可省略**，省略时读 `mmp/.env` 的 `MMP_USERNAME`/`MMP_PASSWORD` |
| `upload_file(token, file_path)` | 分片上传，返回含 `location` 的结果 |

## 模型 (model_*)

| 工具 | 说明 |
|------|------|
| `model_query_type_list(token)` | 模型类型列表 |
| `model_create(token, name, model_tag, model_type)` | 创建模型 |
| `model_query(token, page, size, is_public, name, ...)` | 查询列表；**size=20 分页**；`name` 名称关键字 |
| `model_get_detail(token, git_id, identifier, owner, is_public)` | 查询详情 |
| `model_update(token, id, git_id, identifier, owner, model_type, model_tag)` | 更新 |
| `model_delete(token, id)` | 删除 |
| `model_query_next_version(token, identifier, owner)` | 下一版本号 |
| `model_get_version_list(token, owner, identifier)` | 版本列表；展示 **版本 + version_desc** |
| `model_create_version(token, git_id, id, identifier, file_path, file_location, name, owner, version, version_desc)` | 创建版本 |

## 代码配置 (code_*)

| 工具 | 说明 |
|------|------|
| `code_create(token, code_repo_name, git_url, git_branch, is_public)` | 创建 |
| `code_query(token, page, size, is_public, code_repo_name)` | 查询；展示列 **id、名称、Git 地址**；地址 `{url} ({branch})` |
| `code_update(token, id, code_repo_name, git_branch, git_url, is_public)` | 更新 |
| `code_delete(token, id)` | 删除 |

## 镜像 (image_*)

| 工具 | 说明 |
|------|------|
| `image_create(token, name, tag_name, description, version_description, path)` | 创建 |
| `image_query(token, page, size, is_public, name)` | 查询 |
| `image_delete(token, id)` | 删除 |

## 数据集 (dataset_*)

| 工具 | 说明 |
|------|------|
| `dataset_query_data_types(token, category_id)` | 数据类型/分类 |
| `dataset_create(token, name, data_tag, data_type)` | 创建 |
| `dataset_query(token, page, size, is_public, name, ...)` | 查询列表；**size=20 分页**；`name` 名称关键字 |
| `dataset_get_detail(token, git_id, owner, name, identifier, is_public)` | 详情 |
| `dataset_update(token, id, name, identifier, ...)` | 更新元数据 |
| `dataset_update_desc(token, git_id, identifier, description)` | 更新简介 |
| `dataset_publish(token, id, name)` | 发布 |
| `dataset_query_next_version(token, identifier, owner)` | 下一版本号 |
| `dataset_create_version(token, git_id, id, identifier, file_path, file_location, name, owner, version, version_desc)` | 创建版本 |
| `dataset_get_version_list(token, owner, identifier)` | 版本列表；展示 **版本 + version_desc** |
| `dataset_delete_version(token, git_id, owner, identifier, relative_paths, version)` | 删版本 |
| `dataset_delete(token, id)` | 删数据集 |
| `dataset_download_all(token, name, git_id, version, identifier, owner, is_public)` | 下载全部 |
| `dataset_download_single(token, url)` | 下载单文件 |

## 响应字段映射

### login 成功

```
token = response.data.access_token
```

### create_model / dataset_create 成功

```
id = data.id
git_id = data.git_id
identifier = data.identifier
owner = data.create_by 或 username
```

### upload_file 成功

```
file_location = result.location
```

### query_next_version 成功

```
version = data（具体字段以 API 返回为准）
```

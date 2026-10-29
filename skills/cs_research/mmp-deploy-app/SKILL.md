---
name: mmp-deploy-app
description: >-
  协同开发平台应用一键部署（unified mmp-platform MCP）。触发词：创建应用、部署应用、发布应用、
  智算应用、模型推理服务、在线推理、部署推理、上线模型服务等。用户只需选择模型（平台/上传）、
  代码配置、镜像和资源，其余步骤（应用创建、描述、版本号等）由 Skill 自动完成。
  Login first；poll_service_deploy_progress until terminal.
---

# 应用一键部署

通过统一 MCP **`mmp-platform`** 在协同开发平台**快速部署推理服务**。只需用户选择模型、代码配置、镜像和资源，其余自动完成。

使用本 Skill 前先 `login()` 登录。部署后自动监控至终态。

## 前置条件

| 项 | 说明 |
|----|------|
| **MCP 服务** | 统一 **`mmp-platform`**，见 `mmp-unified/config/config.json` 的 `MCP_PUBLIC_URL` |
| 平台登录 | 优先 **`login()`** 无参（`mmp/.env` 的 `MMP_USERNAME`/`MMP_PASSWORD`） |
| JSM 资源/镜像 | 服务端配置；**`list_resource_*` / `list_deploy_images` 无需传 JSM token** |

## 快速部署流程

完整链路：**登录 → 模型 → 代码配置（自动推断启动命令） → 镜像 → 资源（显存优先推荐） → 自动创建应用 → 自动创建版本 → 监控部署**。

### 第一步：模型来源

用户可以从**平台已有模型**或**上传本地文件**二选一：

**方式 A：从平台选择**
1. **`model_query(token, page=0, size=20, is_public=false)`** 先查个人库
2. 提示可查公开库；用户选定模型
3. **`model_get_version_list(token, owner, identifier)`** → 用户选版本
4. **`model_get_detail(token, git_id, owner, identifier, id, name, version)`** → 获取详情

**方式 B：上传本地文件**
1. 用户提供本地文件路径（如 `C:\models\my_model.ckpt`）
2. **`infer_model_metadata(token, file_path)`** 推断模型类型和标签（若失败则问用户）
3. **`model_create(token, name=<文件名>, model_tag=<推断值>, model_type=<推断值>)`** → 得到 model_id、identifier、owner
4. **`upload_file(token, file_path)`** → 得到 file_location
5. **`model_create_version(token, git_id, id, identifier, file_path, file_location, name, owner, version="v1", version_desc="上传部署")`**

保存模型参数：`model_id`、`model_owner`、`model_identifier`、`model_name`、`model_version`、`model_git_id`。

### 第二步：代码配置与自动推断启动命令

用户可以从**已有代码配置**或**提供 Git 地址**二选一。选定后，**自动尝试推断启动命令**，让用户确认。

**方式 A：从已有选择**
1. **`code_query(token, page=0, size=20, is_public=false)`** 先查个人库
2. 展示后提示可查公开库；用户选定 → **`code_config_id`**
3. 自动推断启动命令（见下方说明）

**方式 B：提供 Git 地址**
1. 用户提供 `git_url` + `git_branch`
2. **`code_create(token, code_repo_name, git_url, git_branch, is_public=true)`** → 注册代码配置
3. 自动推断启动命令（见下方说明）

#### 自动推断启动命令

拿到代码仓库后，按优先级尝试以下方法找到入口文件：

**方法 1：通过 Git 平台 API 列出文件树**
- 对于 `openi.pcl.ac.cn`（Gitea 平台）：`GET /api/v1/repos/{owner}/{repo}/contents?ref={branch}`
- 对于 `gitlink.org.cn` 或 `gitlink.ai4mats.com`：同样尝试 Gitea API
- 检查返回的文件列表中是否存在常见入口文件

**方法 2：通过 `git ls-remote` + 查找文件**
- `git ls-remote --refs <git_url>` 确认分支存在
- 尝试通过 API 或 `git archive` 列出文件

**推断规则：**
在文件列表中查找以下 Python 文件（按优先级排序）：

| 优先级 | 文件名 | 典型用途 |
|--------|--------|----------|
| 1 | `app.py` | Flask/FastAPI 推理服务入口 |
| 2 | `inference.py` | 推理脚本 |
| 3 | `predict.py` | 预测脚本 |
| 4 | `main.py` | 通用入口 |
| 5 | `run.py` | 启动脚本 |
| 6 | `serve.py` / `server.py` | 推理服务 |
| 7 | 其他 `.py` 文件（有 `if __name__ == '__main__'` 模式） |

**优先级最高的匹配文件即为推荐启动命令。**

**如果推断成功：** 向用户展示 `"推断启动命令为：app.py，是否正确？"`，用户确认后使用。
**如果推断失败：** 请用户自行填写启动命令。

> 为什么这样做：用户很可能不记得或不确定入口文件名，代码仓库里通常只有 1-2 个可能的入口文件。自动推断能省去一次来回沟通，即使猜错也只是让用户纠正一下文件名，比凭空提问效率高得多。

### 第三步：镜像

- **`list_deploy_images(resource_type=…)`** → 展示 `imageID`、`name`
- 用户选择

> 资源类型由下一步的资源推荐决定。若用户已知想要的资源类型，也可在本步预先限定。

### 第四步：自动推荐资源规格（显存优先）

**不再让用户从资源类型列表中选择**，而是基于以下推理自动推荐：

1. **模型类型推断**：如果上传了模型文件，`infer_model_metadata` 返回的 `model_type` 和 `framework` 会暗示是否需要 GPU（PyTorch/TensorFlow 模型通常需要 GPU）
2. **代码仓库分析**：如果代码仓库中有 `requirements.txt`，检查是否包含 `torch`、`tensorflow`、`cuda` 等 GPU 依赖
3. **兜底**：推理服务绝大多数需要 GPU，默认以 GPU 类型优先

**操作步骤：**

1. **`list_resource_types()`** → 获取所有可用类型
2. 如果推理为 GPU 需求 → `resource_type = "GPU"`；否则根据模型框架选择最匹配的类型
3. **`list_resource_specs(resource_type=推断类型)`** → 获取规格列表
4. **按显存（VRAM）从大到小排序**，展示给用户，**标记推荐项**（显存最大者优先推荐）：

```markdown
| # | 规格 | CPU | 内存 | 显存 | 存储 |
|---|------|-----|------|------|------|
| 1 | A100 ← 推荐 | 8核 | 50GB | **40GB** | 1024GB |
| 2 | V100 | 16核 | 100GB | 32GB | 1024GB |
```

> 推理服务的性能瓶颈通常是显存（模型加载），所以显存最大的规格最通用。把推荐项放在第一条并用 ← 标记，用户一眼就能看到，确认即可。

5. 用户可直接确认推荐规格，也可选择其他规格。

---

### 自动创建应用（无需用户输入）

收集完上述信息后，**自动执行**以下操作：

| 字段 | 生成方式 |
|------|----------|
| `service_name` | `{模型名称}_{YYYYMMDD}`（如 `final_model_20260623`） |
| `service_type` | 调用 **`list_service_types(token)`** → 取第一个可用类型 |
| `tag` | 空字符串（跳过） |
| `description` | `"推理服务：{模型名称} - {模型类型}"` |
| 用户确认 | **不需要**，直接 `service_create(token, service_name, service_type, description)` |

保存返回的 **`service_id`**。

### 自动创建版本（无需用户确认）

自动生成版本参数：

| 字段 | 生成方式 |
|------|----------|
| `version` | `"v1"`（首次）或调用 `list_service_versions` 查询后增量 |
| `deploy_type` | `"web"`（默认） |
| `command` | 用户第二步确认的启动命令 |
| 汇总确认 | **不需要**，直接调用 `service_prepare_version` + `service_create_version` |

`service_create_version` 的 `version_payload_json` **必须**为 `service_prepare_version` 返回 JSON 中的 **`payload` 字段**（不要传整个 prepare 结果，也不要去掉 `service_id`）。

### 禁止重复创建版本

- `service_create_version` 报错时，**先**调用 `list_service_versions` 确认版本是否已创建
- 若目标 `version` 已存在（含 `already_exists: true`），**禁止**再次调用 `service_create_version`
- 版本已存在但部署未启动时，改用 `service_restart_version`；监控进度用 `poll_service_deploy_progress`
- 返回含 `track_error` 时表示创建已成功、仅启动/查询失败，**禁止**重试创建

---

## 部署输出流程

调用 `service_create_version` 后，按三阶段推送：

### 阶段 1 — 版本已提交（立即）

```markdown
## 应用版本已提交

| 项 | 值 |
|----|-----|
| 应用 | {service_name}（ID: {service_id}） |
| 版本 | {version}（version_id: {version_id}） |
| 模型 | {model_name}（{model_version}） |
| 资源 | {resource_type} / {resource_spec_name} |
| 镜像 | {image_name} |
| 启动命令 | {command} |
| 当前状态 | {status} |

正在监控部署进度…
```

### 阶段 2 — 状态更新（循环）

**每 30 秒**调用 `poll_service_deploy_progress(token, service_id, version, version_id, last_status)`。

仅在状态变化时向用户推送（避免空刷屏）：

```markdown
### 部署状态更新 {time}

- 版本状态：**{status}**
```

`should_continue=false` 时进入阶段 3。

### 阶段 3 — 部署终态

```markdown
## 部署完成

| 项 | 值 |
|----|-----|
| 状态 | {completion_summary.status_display} |
| 应用 | {service_name}（ID: {service_id}） |
| 版本 | {version}（version_id: {version_id}） |
| 访问地址 | {completion_summary.service_url 或「—」} |
```

失败时**不要**擅自改参重部署，说明原因并请用户决定。

---

## MCP 工具映射

| 操作 | 工具 |
|------|------|
| 登录 | `login` |
| 应用类型 | `list_service_types` |
| 创建应用 | `service_create` |
| 模型查询/版本/详情 | `model_query` / `model_get_version_list` / `model_get_detail` |
| 模型上传/创建 | `infer_model_metadata` / `model_create` / `upload_file` / `model_create_version` |
| 代码配置（查/建） | `code_query` / `code_create` |
| 代码仓库 API 探测 | `webfetch`（调用 Gitea/GitLink API） |
| 资源类型/规格 | `list_resource_types` / `list_resource_specs` |
| 部署镜像 | `list_deploy_images` |
| 组装 & 创建版本 | `service_prepare_version` → `service_create_version` |
| 部署轮询 | `poll_service_deploy_progress` |

## 禁止事项

- **禁止**在收集完模型/代码/镜像/资源前调用 `service_create`
- **禁止**向用户索要应用名称、类型、描述、标签
- **禁止**向用户索要版本号（自动生成）
- **禁止**先展示全部资源类型让用户选 — 应自动推断并推荐
- **禁止**在能推断启动命令时直接问用户 — 先推断再让用户确认
- **禁止**创建版本后静默等到终态
- **禁止** `model_query` 使用 `size=2000`
- **禁止**向用户索要 JSM token
- **禁止**失败后擅自改参重部署
- **禁止** `service_create_version` 报错后未查 `list_service_versions` 就重试创建

## 相关文档

- [reference-api.md](reference-api.md) — HTTP 接口样例
- [reference.md](reference.md) — 工具速查

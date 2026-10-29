# 应用部署 MCP 工具速查（mmp-deploy-app）

基址与鉴权同 [mmp-training-workflow/reference.md](../mmp-training-workflow/reference.md)。

## 应用 API

| 操作 | 方法 | 路径 | MCP 工具 |
|------|------|------|----------|
| 应用类型 | GET | `/api/mmp/service/getServiceTypes` | `list_service_types` |
| 创建应用 | POST | `/api/mmp/service` | `service_create` |
| 应用详情 | GET | `/api/mmp/service/serviceDetail/{id}` | `service_get_detail` |
| 版本列表 | GET | `/api/mmp/service/version/list?…` | `list_service_versions` |
| 创建版本 | POST | `/api/mmp/service/version` | `service_create_version` |
| 重启版本 | POST | `/api/mmp/service/version/restart` | `service_restart_version` |
| **部署轮询** | （查版本列表 status） | — | **`poll_service_deploy_progress`** |

## 与训练共用（JSM，服务端自动鉴权）

| 操作 | MCP 工具 |
|------|----------|
| 资源类型 | `list_resource_types` |
| 资源规格 | `list_resource_specs` |
| 部署镜像（全量） | `list_deploy_images` |

## 与资源 Skill 共用（须 login token）

| 操作 | MCP 工具 |
|------|----------|
| 登录 | `login` |
| 代码配置 | `code_query` |
| 模型列表 | `model_query` |
| 模型版本 | `model_get_version_list` |
| 模型详情 | `model_get_detail`（须 `id/name/version`） |

## 推荐调用链（简化版）

```
login
  → 模型（平台查询/上传文件）
  → 代码配置（已有/Git地址）
  → 镜像选择
  → 资源选择
  → （自动）list_service_types → 取首个类型
  → （自动）service_create（名称=模型名_日期，描述自动生成）
  → （自动）service_prepare_version + service_create_version
  → poll_service_deploy_progress（循环至 should_continue=false）
```

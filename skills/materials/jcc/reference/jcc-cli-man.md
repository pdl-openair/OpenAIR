# JCC CLI 使用说明

## 概述

jcc cli是JCWeaver中间件v2 HTTP API的命令行工具，用于在云际计算平台上执行各种操作。

## 全局参数

| 参数 | 环境变量 | 说明 | 默认值 |
|------|----------|------|--------|
| `--base-url` | JCC_BASE_URL | 中间件根URL | https://jcc.jointcloud.net |
| `--jcc-url` | JCC_JCC_URL | JCC根URL，用于/pcm/*和/jsm/*端点 | - |
| `--token` | JCC_TOKEN | Authorization token | - |
| `--user-id` | JCC_USER_ID | 用户ID | - |
| `--output` | - | 输出格式: json \| text | json |
| `--verbose` | - | 打印请求详情 | - |
| `--dry-run` | - | 仅打印请求体，不发起网络调用 | - |
| `--timeout` | - | HTTP超时 | 5m |
| `--notify-timeout` | - | notify超时 | 30m |

## 可用命令

### 1. login - 用户登录

调用/jcc-admin/admin/login获取JWT token，解析并返回userID和token。

```bash
jcc login --username <user> [--password <pwd>] [--base-url <url>]
```

**参数**:
- `--username`: 用户名（必填，默认admin）
- `--password`: 密码（必填）
- `--base-url`: 服务端地址（默认https://jcc.jointcloud.net）

**示例**:
```bash
$login = jcc login --username admin | ConvertFrom-Json
$token = $login.token
$uid = $login.userID
```

---

### 2. hpc - 超算任务管理

HPC一站式任务管理（创建/结果/日志/状态）

```bash
jcc hpc [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `create` | 创建并提交HPC任务 |
| `cluster-list` | 查询支持指定应用类型的集群列表 |
| `predict` | 预测 POSCAR 结构的能量值 |
| `vaspkit` | 生成 VASP 输入文件(INCAR/KPOINTS/POTCAR) |
| `log` | 查看HPC任务日志 |
| `result` | 查看HPC任务结果文件列表 |
| `result download` | 下载HPC任务结果（zip） |
| `status` | 查看HPC任务实时状态 |

**查询集群**:
```bash
jcc hpc cluster-list --apptype <app>
```

**生成VASP输入文件**:
```bash
jcc hpc vaspkit --path <poscar-file-path>
```

**预测能量值**:
```bash
jcc hpc predict --path <poscar-file-path>
```

**创建任务**:

根据应用类型区分两套流程：
- **VASP 流程**: 查集群 -> 查包 -> 校验文件(POSCAR/INCAR/KPOINTS/POTCAR) -> 提交任务
- **非 VASP 流程**: 查集群 -> 查包 -> 查文件 objectID -> 提交任务（需 `--file-name`）

```bash
# VASP
jcc hpc create --name <task> --cluster-name <cluster> --package-name <pkg> --apptype vasp

# 非 VASP（如 lammps）
jcc hpc create --name <task> --cluster-name <cluster> --package-name <pkg> --apptype <app> --file-name <file>
```

**参数**:
- `--name`: 任务名称（必填）
- `--cluster-name`: 目标集群名称（必填）
- `--package-name`: 已有包名称（必填）
- `--apptype`: 应用类型，如 vasp、lammps（必填）
- `--file-name`: 包内文件名（非 VASP 应用必填）
- `--nodes`: 计算节点数量（默认1）
- `--ntasks`: 每个节点任务数（默认4）
- `--param`: 运行参数 KEY=VALUE（可选，可多次）

---

### 3. aitrain - 智算训练任务管理

```bash
jcc aitrain [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `submit` | 提交智算训练任务 |
| `query` | 查询智算训练任务列表 |
| `detail` | 查询智算训练任务详情 |
| `log` | 查询智算训练任务日志 |
| `result` | 查询智算训练任务结果 |
| `stop` | 停止智算训练任务 |
| `cardtype` | 查询智算训练可用加速卡类型及资源配额 |
| `cluster-list` | 查询可创建智算训练任务的集群列表 |

**查询可用加速卡类型**:
```bash
jcc aitrain cardtype [--image-tags <tags>]
```

**参数**:
- `--image-tags`: 镜像标签过滤（可选）

**提交训练任务**:
```bash
jcc aitrain submit \
  --name <task-name> \
  --cluster-id <id> | --cluster-name <name> \
  --code-binding-id <id> | --code-name <name> \
  --dataset-binding-id <id> | --dataset-name <name> \
  --model-binding-id <id> | --model-name <name> \
  --image-id <id> | --image-name <name> \
  --resource <spec>
```

**必填参数**:
- `--name`: 任务名称
- `--cluster-id` / `--cluster-name`: 目标集群（必须使用 name 类参数，ID 类参数虽返回成功但任务不入表）
- `--code-binding-id` / `--code-name`: 代码绑定（必须使用 name 类参数，ID 类参数虽返回成功但任务不入表；名称格式：父算法名称/子算法名称，如 finetuning02/finetuning0201）
- `--dataset-binding-id` / `--dataset-name`: 数据集绑定（必须使用 name 类参数，ID 类参数虽返回成功但任务不入表）
- `--resource`: 资源规格，格式: TYPE:TYPE:NUMBER*NAME(显存:XXGiB),CPU:N,内存:NGiB:1（可多次），如 GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1

**可选参数**:
- `--command`: 运行命令
- `--description`: 任务描述
- `--env`: 环境变量 KEY=VALUE（可多次）
- `--param`: 运行参数 KEY=VALUE（可多次）
- `--schedule-strategy`: 调度策略（默认dataLocality）
- `--data`: 完整jobSet JSON
- `--data-file`: 完整jobSet JSON文件

---

### 4. aiinfer - 智算推理任务管理

```bash
jcc aiinfer [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `submit` | 提交智算推理任务 |
| `query` | 查询智算推理任务列表 |
| `detail` | 查询智算推理任务详情 |
| `stop` | 停止智算推理任务 |
| `cardtype` | 查询智算推理可用加速卡类型及资源配额 |
| `cluster-list` | 查询可创建智算推理任务的集群列表 |

**查询可用加速卡类型**:
```bash
jcc aiinfer cardtype [--image-tags <tags>]
```

**参数**:
- `--image-tags`: 镜像标签过滤（可选）

**提交推理任务**:
```bash
jcc aiinfer submit \
  --name <task-name> \
  --cluster-id <id> | --cluster-name <name> \
  --code-binding-id <id> | --code-name <name> \
  --model-binding-id <id> | --model-name <name> \
  --image-id <id> | --image-name <name> \
  --resource <spec>
```

**必填参数**:
- `--name`: 任务名称
- `--cluster-id` / `--cluster-name`: 目标集群（必须使用 name 类参数，ID 类参数虽返回成功但任务不入表）
- `--code-binding-id` / `--code-name`: 代码绑定（必须使用 name 类参数，ID 类参数虽返回成功但任务不入表；名称格式：父算法名称/子算法名称，如 finetuning02/finetuning0201）
- `--model-binding-id` / `--model-name`: 模型绑定（必须使用 name 类参数，ID 类参数虽返回成功但任务不入表）
- `--resource`: 资源规格，格式: TYPE:TYPE:NUMBER*NAME(显存:XXGiB),CPU:N,内存:NGiB:1（可多次），如 GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1

**可选参数**:
- `--image-id`: 镜像ID
- `--binding-id`: 推理服务绑定ID
- `--command`: 运行命令
- `--description`: 任务描述
- `--env`: 环境变量 KEY=VALUE（可多次）
- `--param`: 运行参数 KEY=VALUE（可多次）
- `--schedule-strategy`: 调度策略（默认networkPriority）
- `--schedule-type`: 调度类型如 QoS
- `--resource-id` / `--resource-name`: 资源ID或名称（二选一，来自 cluster-list 查询结果）
- `--sub-model-binding-id` / `--sub-model-name`: 子模型绑定ID或名称（二选一）
- `--only-create`: 仅创建不启动
- `--local-job-id`: 本地任务ID（默认1）
- `--data`: 完整 jobSet JSON（优先级低于 --data-file）
- `--data-file`: 完整 jobSet JSON 文件

---

### 5. cloud - 云资源管理

```bash
jcc cloud [command]
```

#### 5.1 container - 云容器管理

```bash
jcc cloudcontainer [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `create` | 创建云容器 |
| `query` | 查询容器列表或详情 |
| `log` | 查看容器日志 |
| `delete` | 删除容器 |
| `cluster-list` | 查询可创建云容器的集群列表 |

**示例**:
```bash
jcc cloudcontainer create --cluster-name <name> --name <name> --image <image> --cpu <cpu> --memory <memory>
jcc cloudcontainer create --cluster-name <name> --name <name> --image <image> --cpu <cpu> --memory <memory> --port 8080 --replica 2 --container-group <group>
jcc cloudcontainer query --page-num 1 --page-size 10
jcc cloudcontainer query --id <container-id>
jcc cloudcontainer query --name <container-name>
jcc cloudcontainer log --name <container-name>
jcc cloudcontainer delete --name <container-name>
jcc cloudcontainer cluster-list
jcc cloudcontainer cluster-list --type GPU
```

#### 5.2 instance - 虚拟机实例管理

```bash
jcc cloudinstance [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `create` | 创建虚拟机实例 |
| `query` | 查询虚拟机实例列表或详情 |
| `operate` | 对虚拟机实例执行操作（启动/停止/删除等） |
| `cluster-list` | 查询可创建虚拟机实例的资源规格列表（获取flavor-id） |

**cluster-list 参数**:
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--type` | 资源类型（CPU/GPU/NPU/MLU） | CPU |

**create 参数**:
| 参数 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| `--cluster-name` | 二选一 | 集群名称 | - |
| `--cluster-id` | 二选一 | 集群ID | - |
| `--host-name` | 是 | 主机名 | - |
| `--instance-type` | 是 | 实例类型（即sourceKey，来自cluster-list的SOURCE KEY列） | - |
| `--password` | 是 | 登录密码 | - |
| `--image-id` | 是 | 镜像标识（即External ID，来自image list的External ID列） | - |
| `--system-disk-size` | 是 | 系统盘大小(GB) | - |
| `--username` | 否 | 用户名 | root |
| `--charge-type` | 否 | 计费类型 | PostPaid |
| `--disk-category` | 否 | 系统盘类型 | cloud_ssd |
| `--disk-level` | 否 | 性能级别 | PL0 |
| `--replica` | 否 | 副本数 | 1 |
| `--security-group-id` | 否 | 安全组ID | - |

**示例**:
```bash
jcc cloudinstance cluster-list --type CPU
jcc cloudinstance create --cluster-name "阿里云" --host-name node-1 --instance-type ecs.t5-lc2m1.nano --password MyPass123 --image-id ubuntu_20_04_x64_20G_alibase_20251031.vhd --system-disk-size 20
jcc cloudinstance query
jcc cloudinstance query --host-name node-1
jcc cloudinstance query --host-name node-1 --page-num 1 --page-size 10
jcc cloudinstance query --status running
jcc cloudinstance operate --host-name node-1 --operate-type start
jcc cloudinstance operate --host-name node-1 --operate-type stop
jcc cloudinstance operate --host-name node-1 --operate-type delete
```

---

### 6. algo - 算法管理

```bash
jcc algo [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `create` | 创建算法包并上传 |
| `fork` | 创建子算法到不同集群 |
| `query` | 查询算法包列表或详情 |
| `view` | 查看算法文件内容 |
| `delete` | 删除算法包 |
| `cluster-list` | 查询可创建算法的集群列表 |

**创建算法包**:
```bash
jcc algo create --name <name> --path <path>
```

**参数**:
- `--name`: 算法名称（必填）
- `--path`: 本地文件或目录路径（必填）

**创建子算法**:
```bash
jcc algo fork --parent-name <name> --cluster-name <name> --description <name>
```

**参数**:
- `--parent-name`: 父算法包名称（必填）
- `--cluster-name`: 目标集群名称（必填）
- `--description`: 子算法名称（必填）

**查询算法包**:
```bash
jcc algo query [--package-name <name>]
```

**查看算法文件**:
```bash
jcc algo view --package-name <name> [--description <name>]
```

**删除算法包**:
```bash
jcc algo delete --package-name <name> [--force]
```

**查询集群列表**:
```bash
jcc algo cluster-list
```

---

### 7. dataset - 数据集管理

```bash
jcc dataset [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `create` | 创建数据集（建包+本地上传+notify+同步定版） |
| `query` | 查询数据集列表或详情 |
| `schedule` | 调度数据集到集群 |
| `cluster-list` | 查询可创建数据集的集群列表 |

**创建数据集**:
```bash
jcc dataset create --name <name> --path <path> [--clusters <id> ...]
```

---

### 8. image - 镜像管理

```bash
jcc image [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `list` | 查询可用镜像列表，支持按 tag、name、flavorId、providerId 过滤 |

**查询镜像列表**:
```bash
jcc image list [--tag <tag>] [--name <name>] [--flavor-id <id>] [--provider-id <id>] [--page <page>] [--size <size>]
```

**参数**:
- `--tag`: 按镜像标签模糊查询（可选）
- `--name`: 按镜像名称模糊查询（可选）
- `--flavor-id`: 按资源规格ID过滤（可选，精确匹配）
- `--provider-id`: 按Provider ID过滤（可选，精确匹配）
- `--page`: 分页页码（默认 1）
- `--size`: 每页数量（默认 10）

**输出示例**:
```bash
total: 865  page: 1  size: 10

=== 镜像列表 ===
ID   External ID                       Name                                     Tag                                      Kind  Provider        Status     Description              Created
2692 JupyterPyTorchCUDA12Python3.11    Jupyter PyTorch CUDA12 Python 3.11       cuda12-python-3.11                       ai    电信机房-自建jupyterHub  available  pytorch-notebook        2026-07-06 08:53:37
2262 pytorch_ascend_cann8.5            pytorch_ascend_cann8.5                   puppteer                                 ai    中国算力网-云脑II集群   available  成功部署qwen3.5和GLM    2026-04-09 01:39:16
995  pytorch_2.1.0-cann_8.0.rc3        pytorch_2.1.0-cann_8.0.rc3-py_3.9        pytorch_2.1.0-cann_8.0.rc3-py_3.9        ai    中国算力网-启智社区集群   available                       2026-03-18 01:19:28
```
其中 `External ID` 为底层云平台的镜像标识，`Name` 为平台可见的镜像名称。

---

### 9. model - 模型管理

```bash
jcc model [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `create` | 创建模型（建包+本地上传+notify+同步定版） |
| `query` | 查询模型列表或详情 |
| `schedule` | 调度模型到集群 |
| `cluster-list` | 查询可创建模型的集群列表 |

**创建模型**:
```bash
jcc model create --name <name> --path <path> [--clusters <id> ...]
```

---

### 10. commonfile - HPC文件包管理

```bash
jcc commonfile [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `create` | 创建HPC文件包并上传 |
| `query` | 查询HPC文件包列表或详情 |
| `schedule` | 将已有包调度到集群 |
| `delete` | 删除HPC文件包 |
| `cluster-list` | 查询可上传文件的集群列表 |

**创建文件包**:
```bash
jcc commonfile create --name <name> --path <path> --cluster-name <cluster>
```

---

### 11. jobset - JobSet管理

```bash
jcc jobset [command]
```

**子命令**:
| 命令 | 说明 |
|------|------|
| `submit` | 提交JobSet |

**提交JobSet**:
```bash
jcc jobset submit --data-file <json-file>
```

---

## 输出格式

命令执行结果以JSON格式输出，包含以下字段：
- `status`: 执行状态（success/failed）
- `message`: 执行消息
- `data`: 返回数据
- `taskId`: 任务ID（如有）

## 错误处理

常见错误码：
- 1001: 未登录（需先执行jcc login）
- 1002: 参数错误（检查必填参数）
- 1003: 资源不存在（确认ID是否正确）
- 1004: 权限不足（联系管理员）
- 2001: 网络错误（检查网络连接）
- 2002: 服务端错误（稍后重试）

## 使用技巧

### 设置环境变量

```bash
$env:JCC_BASE_URL = "https://myhost.com"
$env:JCC_TOKEN = "my-token"
$env:JCC_USER_ID = "123"
```

### 查看命令详情

```bash
jcc <command> --help
jcc <command> <subcommand> --help
```

### 输出格式切换

```bash
jcc hpc create --name mytask --cluster-name HPCSlurmArm --package-name test --apptype vasp --output text
```

### 调试模式

```bash
jcc aitrain submit --name mytrain --cluster-name xxx --code-name 父算法/子算法 --dataset-name xxx --image-name xxx --resource GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1 --verbose --dry-run
```

> 注意：name 类参数在 --dry-run 模式下验证会失败，但实际提交正常工作
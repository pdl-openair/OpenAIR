# jcc CLI 使用说明

本文档按当前实现整理所有 CLI 命令。
windows版本下载地址：https://jcc.jointcloud.net/jsm/v2/storage/public/download?objectID=144433
linux版本下载地址：https://jcc.jointcloud.net/jsm/v2/storage/public/download?objectID=144432

## 命令树

```
jcc
├── login
├── hpc
│   ├── create
│   ├── predict
│   ├── vaspkit
│   ├── log
│   ├── status
│   ├── result
│   └── result download
├── commonfile
│   ├── create
│   ├── query
│   ├── schedule
│   └── delete
├── dataset
│   ├── create
│   ├── query
│   └── schedule
├── model
│   ├── create
│   ├── query
│   └── schedule
├── cloud
│   ├── container
│   │   ├── query
│   │   ├── log
│   │   └── delete
│   └── instance
│       ├── query
│       └── operate
├── jobset
│   └── submit
├── train
│   ├── submit
│   ├── query
│   ├── detail
│   ├── log
│   ├── result
│   ├── resource-range
│   └── resource-query
└── inference
    ├── submit
    ├── query
    ├── detail
    ├── log
    └── stop
```

---

## 全局参数

| 参数 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--jcc-url` | `JCC_JCC_URL` | — | JCC 根 URL，用于 `/pcm/*` 和 `/jsm/*` 端点 |
| `--token` | `JCC_TOKEN` | — | Authorization token |
| `--output` | — | `json` | 输出格式：`json` \| `text` |
| `--verbose` | — | `false` | 打印请求 URL 与原始响应片段 |
| `--dry-run` | — | `false` | 仅打印请求体，不发起网络调用 |
| `--timeout` | — | `5m` | 除 upload/notify 外的 HTTP 超时 |
| `--notify-timeout` | — | `30m` | `POST /v2/files/upload/notify` 超时 |

所有全局参数对所有子命令生效。环境变量无法满足需求时可改用 flag 显式传值。

---

## 环境初始化

```powershell
$env:JCC_BASE_URL = "https://jcc.jointcloud.net"
$login = jcc login --username iflytek --password "" | ConvertFrom-Json
$env:JCC_TOKEN = $login.token
```

---

## jcc login —— 登录认证

调用 `/jcc-admin/admin/login` 获取 JWT token，从 payload 中提取 userID。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--username` | string | `admin` | 用户名 |
| `--password` | string | — | 密码 |
| `--base-url` | string | `https://jcc.jointcloud.net` | 服务端地址 |

```powershell
# 登录获取 token
jcc login --username iflytek --password "iflytek@123"
```

---

## jcc hpc —— 超算任务管理

| 涉及接口 | 说明 |
| --- | --- |
| `GET /jsm/v2/storage/cluster/detail` | 按集群名查 clusterID |
| `POST /files/upload/query` | 按包名查 packageID / 按 packageID 查包内文件列表 |
| `POST /jobs/submit` | 提交 HPC 任务 |
| `GET /pcm/v1/core/task/list` | 按名称解析 taskID |
| `GET /pcm/v1/core/task/details` | 按名称查任务详情（status） |
| `GET /pcm/v1/hpc/jobLogs/<taskID>` | 查任务 stdout / stderr 日志（log） |
| `POST /jsm/v2/jobs/results` | 查任务结果文件列表（result） |
| `POST /jsm/storage/presign` | 预签名批量下载结果 zip（result download） |
| `POST /apis/kit/api/alignn/predict` | ALIGNN 能量预测 |
| `POST /apis/kit/api/vaspkit/generate` | VASPKIT 生成 VASP 输入文件 |

### hpc create —— 创建并提交超算任务

流程：查集群(1/4) → 查包(2/4) → 校验文件(3/4) → 提交任务(4/4)

文件校验规则：
- 必须包含 POSCAR，缺失则直接报错退出
- 必须同时包含 INCAR、KPOINTS、POTCAR，缺失任一则提示使用 `jcc hpc vaspkit` 命令生成

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 任务名称（必填） |
| `--cluster-name` | string | — | 目标集群名称（必填） |
| `--package-name` | string | — | 已有包名称（必填） |
| `--apptype` | string | — | 应用类型，如 vasp（必填） |
| `--nodes` | string | `1` | 计算节点数量 |
| `--ntasks` | string | `4` | 总任务数 |
| `--param` | stringArray | — | 运行参数 `KEY=VALUE`（可多次） |

```powershell
# 提交超算任务
jcc hpc create --name "vasp-task0612" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "vasp-test0612"

# 自定义计算资源
jcc hpc create --nodes 2 --ntasks 8 --name "vasp-big" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "vasp-test0612"

# 传入运行参数（会写入 jobSetInfo.jobs[].parameters）
jcc hpc create --name "vasp-task" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "vasp-test0612" --param ENCUT=520 --param ISIF=3
```

#### 典型工作流

```powershell
# 1. 上传本地 VASP 输入文件到平台
jcc commonfile create --name "vasp-061501" --cluster-name "HPCSlurmArm" --path "D:\band_zqj\"

# 2. （可选）用 POSCAR 预测能量值
jcc hpc predict --path "D:\band_zqj\POSCAR"

# 3. （可选）若缺少 INCAR/KPOINTS/POTCAR，用 POSCAR 生成
jcc hpc vaspkit --path "D:\band_zqj\POSCAR"

# 4. 提交超算任务
jcc hpc create --name "task-061501" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "vasp-061501"

# 4b. 带运行参数提交
jcc hpc create --name "task-061501" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "vasp-061501" --param ENCUT=520 --param ISIF=3

# 5. 查看任务状态
jcc hpc status --task-name "task-061501"

# 6. 查看任务日志
jcc hpc log --task-name "task-061501"

# 7. 查看结果文件列表
jcc hpc result --task-name "task-061501"

# 8. 下载结果
jcc hpc result download --task-name "task-061501" --output "./result.zip"
```

#### 容错示例

```powershell
# 场景A: package 内缺少 POSCAR —— 预期在 [3/4] 报错
jcc commonfile create --name "no-poscar" --cluster-name "HPCSlurmArm" --path "D:\no_poscar\"
jcc hpc create --name "task-fail" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "no-poscar"
# 错误: package 内没有 POSCAR 文件，请确保 package 包含有效的 POSCAR

# 场景B: package 内仅有 POSCAR —— 预期在 [3/4] 报错，提示使用 vaspkit 修复
jcc commonfile create --name "poscar-only" --cluster-name "HPCSlurmArm" --path "D:\only_poscar\"
jcc hpc create --name "task-fail2" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "poscar-only"
# 错误: package 内缺少文件: INCAR、KPOINTS、POTCAR，请使用 jcc hpc vaspkit 命令生成这三个文件

# 修复后重新上传提交
jcc hpc vaspkit --path "D:\only_poscar\POSCAR"
jcc commonfile create --name "poscar-full" --cluster-name "HPCSlurmArm" --path "D:\only_poscar\"
jcc hpc create --name "task-ok" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "poscar-full"
```

### hpc predict —— 预测能量值

将本地 POSCAR 文件上传至 ALIGNN 接口，返回能量预测结果。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--path` | string | — | POSCAR 文件路径（必填） |

```powershell
jcc hpc predict --path "D:\band_zj\POSCAR"
```

### hpc vaspkit —— 生成 VASP 输入文件

将本地 POSCAR 文件上传至 VASPKIT 接口，在 POSCAR 所在目录生成 INCAR、KPOINTS、POTCAR 三个文件。

默认参数：`kpr=0.04`、`symprec=0.00001`、`format=json`

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--path` | string | — | POSCAR 文件路径（必填） |

```powershell
jcc hpc vaspkit --path "D:\band_zj\POSCAR"
```

### hpc log —— 查看任务运行日志

调用 `GET /pcm/v1/hpc/jobLogs/<taskID>` 获取 JobID、WorkDir 及 stdout/stderr 输出。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-name` | string | — | 任务名称（必填） |

```powershell
jcc hpc log --task-name "vasp-task0612"
```

### hpc status —— 查看任务详情

调用 `GET /pcm/v1/core/task/details?name=<taskName>` 返回完整任务详情 JSON。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-name` | string | — | 任务名称（必填） |

```powershell
jcc hpc status --task-name "vasp-task0612"
```

### hpc result —— 查看结果文件列表

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-name` | string | — | 任务名称（必填） |

```powershell
jcc hpc result --task-name "vasp-task0612"
```

### hpc result download —— 下载结果文件

流程：名称解析 taskID → 查询结果获取 packageID → 预签名 → 下载 zip

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-name` | string | — | 任务名称（必填） |
| `--output` | string | `<taskID>.zip` | 输出文件路径 |

```powershell
# 下载到默认路径
jcc hpc result download --task-name "vasp-task0612"

# 指定输出路径
jcc hpc result download --task-name "vasp-task0612" --output "./result.zip"
```

---

## jcc commonfile —— HPC 文件包管理

数据类型固定为 `HPCSlurm`。

| 涉及接口 | 说明 |
| --- | --- |
| `GET /jsm/v2/storage/cluster/detail` | 按集群名查 clusterID/storageID |
| `POST /package/create` | 创建包 |
| `POST /storage/presign` | 获取预签名上传 URL |
| `POST /files/upload/notify` | 通知上传完成 |
| `POST /files/upload/query` | 按包名查 packageID |
| `POST /bindings/query` | 查询绑定列表 |
| `POST /jsm/v2/bindings/do/async` | 异步调度绑定 |
| `GET /jsm/jobSet/deletePackage` | 删除包 |

### commonfile create —— 创建并上传文件包

流程：查集群(1/5) → 创建包(2/5) → 逐个文件：预签名(3/5) → 上传(4/5) → 通知(5/5)

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 包名称（必填） |
| `--path` | string | — | 本地文件或目录路径（必填） |
| `--cluster-name` | string | — | 目标集群名称（必填） |
| `--description` | string | — | 包描述（选填） |

```powershell
# 创建并上传个人文件（超算输入文件）
jcc commonfile create --name "vasp-test0612" --cluster-name "HPCSlurmArm" --path "D:\band_zj\"
```

### commonfile query —— 查询文件包列表或详情

不带 `--package-name` 返回列表，带 `--package-name` 查详情。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--package-name` | string | — | 包名称（选填） |

```powershell
# 查询文件包列表
jcc commonfile query

# 按名称查询文件包详情
jcc commonfile query --package-name "vasp-test0612"
```

### commonfile schedule —— 调度已有包到集群

流程：查包名 → 查集群名 → 异步绑定

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--cluster-name` | string | — | 目标集群名称（必填） |
| `--package-name` | string | — | 包名称（必填） |

```powershell
jcc commonfile schedule --cluster-name "HPCSlurmArm" --package-name "vasp-test0612"
```

### commonfile delete —— 删除文件包

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--package-name` | string | — | 包名称（必填） |
| `--force` | bool | `false` | 跳过确认提示 |

```powershell
jcc commonfile delete --package-name "vasp-test0612" --force
```

---

## jcc dataset —— 数据集管理

| 涉及接口 | 说明 |
| --- | --- |
| `POST /package/create` | 创建包 |
| `POST /storage/presign` | 预签名上传 |
| `POST /files/upload/notify` | 通知上传完成 |
| `POST /bindings/do/sync` | 同步定版 |
| `POST /bindings/query` | 查询绑定列表 |
| `GET /bindings/detail` | 查询绑定详情 |

### dataset create —— 创建数据集

流程：建包 → 本地上传 → notify → 同步定版

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 数据集名称（必填） |
| `--path` | string | — | 本地文件或目录路径（必填） |
| `--clusters` | stringSlice | `[]` | 目标集群 ID，可多次指定 |

```powershell
# 创建数据集
jcc dataset create --name "my-dataset" --path "D:\data\"

# 创建并指定集群
jcc dataset create --name "my-dataset" --path "D:\data\" --clusters "cluster-01" --clusters "cluster-02"
```

### dataset query —— 查询数据集

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--id` | int64 | `0` | 定版/数据集 ID；为空则列表 |

```powershell
# 查询数据集列表
jcc dataset query

# 按 ID 查询详情
jcc dataset query --id 1
```

### dataset schedule —— 调度数据集到集群

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--cluster-id` | string | — | 目标集群 ID（必填） |
| `--package-id` | int64 | `0` | 数据集包 ID（或环境变量 `JCC_DATASET_PACKAGE_ID`） |
| `--name` | string | `dataset` | 绑定名称 |

```powershell
jcc dataset schedule --cluster-id "cluster-01" --package-id 1
```

---

## jcc model —— 模型管理

与 dataset 共享 `internal/resource` 实现，接口相同，仅 dataType / bindingType / 环境变量不同。

### model create

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 模型名称（必填） |
| `--path` | string | — | 本地文件或目录路径（必填） |
| `--clusters` | stringSlice | `[]` | 目标集群 ID，可多次指定 |

```powershell
jcc model create --name "my-model" --path "D:\model\"
```

### model query

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--id` | int64 | `0` | 定版/模型 ID；为空则列表 |

```powershell
jcc model query
jcc model query --id 1
```

### model schedule

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--cluster-id` | string | — | 目标集群 ID（必填） |
| `--package-id` | int64 | `0` | 模型包 ID（或环境变量 `JCC_MODEL_PACKAGE_ID`） |
| `--name` | string | `model` | 绑定名称 |

```powershell
jcc model schedule --cluster-id "cluster-01" --package-id 1
```

---

## jcc cloud —— 云资源管理

cloud 下包含 `container` 和 `instance` 两个子资源。

### cloud container —— 云容器

| 涉及接口 | 说明 |
| --- | --- |
| `GET /jobs/cloud/list` | 容器列表 |
| `GET /jobs/cloud/detail` | 容器详情 |
| `GET /jobs/cloud/container/log/<id>` | 容器日志 |
| `DELETE /jobs/cloud/container/delete/<id>` | 删除容器 |

#### cloud container query

不带 `--id` 返回分页列表，带 `--id` 返回详情。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--id` | string | — | 容器 ID；指定后查询详情 |
| `--type` | int64 | `0` | 任务类型过滤 |
| `--name` | string | — | 任务名称过滤 |
| `--status` | string | — | 任务状态过滤 |
| `--page-num` | int64 | `0` | 页码 |
| `--page-size` | int64 | `0` | 页大小 |

```powershell
# 查询容器列表
jcc cloud container query
jcc cloud container query --name "mycontainer" --status running

# 查询容器详情
jcc cloud container query --id "<container-id>"
```

#### cloud container log

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--id` | string | — | 容器 ID（必填） |

```powershell
jcc cloud container log --id "<container-id>"
```

#### cloud container delete

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--id` | string | — | 容器 ID（必填） |

```powershell
jcc cloud container delete --id "<container-id>"
```

### cloud instance —— 云实例

| 涉及接口 | 说明 |
| --- | --- |
| `GET /jobs/cloud/instance/page` | 实例列表 |
| `GET /jobs/cloud/instance/get/<id>` | 实例详情 |
| `POST /jobs/cloud/instance/operate` | 实例操作 |

#### cloud instance query

不带 `--id` 返回分页列表，带 `--id` 返回详情。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--id` | string | — | 实例 ID；指定后查询详情 |
| `--host-name` | string | — | 主机名过滤 |
| `--status` | string | — | 实例状态过滤 |
| `--page-num` | int64 | `0` | 页码 |
| `--page-size` | int64 | `0` | 页大小 |

```powershell
# 查询实例列表
jcc cloud instance query
jcc cloud instance query --host-name "node-1" --status running

# 查询实例详情
jcc cloud instance query --id "<instance-id>"
```

#### cloud instance operate

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--id` | stringArray | `[]` | 实例 ID；可多次指定（必填） |
| `--operate-type` | string | — | 操作类型（必填） |

```powershell
# 操作单个实例
jcc cloud instance operate --id "<instance-id>" --operate-type start

# 批量操作
jcc cloud instance operate --id "<id1>" --id "<id2>" --operate-type stop
```

---

## jcc jobset —— JobSet 提交

| 涉及接口 | 说明 |
| --- | --- |
| `POST /jobs/submit` | 提交 JobSet |

### jobset submit

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--data` | string | — | 内联 JSON 数据 |
| `--data-file` | string | — | JSON 文件路径 |

`--data` 和 `--data-file` 至少提供一个；`--data-file` 优先级更高。

```powershell
# 通过文件提交
jcc jobset submit --data-file "./jobset.json"

# 通过内联 JSON 提交
jcc jobset submit --data '{"userID":123,"jobSetInfo":{...}}'
```

---

## jcc train —— 智算训练任务管理

构造 `type=AI` 的 JobSet；taskID 优先。

| 涉及接口 | 说明 |
| --- | --- |
| `POST /jobs/submit` | 提交 / 停止 |
| `GET /jobs/tasks` | 查询列表 |
| `GET /jobs/details` | 查询详情 |
| `GET /jobs/logs` | 查询日志 |
| `POST /jobs/results` | 查询结果 |
| `POST /resource/range` | 资源范围 |
| `POST /resource/query` | 资源查询 |

### train submit

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 任务名称（必填） |
| `--cluster-id` | string | — | 目标集群 ID（必填） |
| `--code-binding-id` | int64 | `0` | 代码绑定 ID（必填） |
| `--dataset-binding-id` | int64 | `0` | 数据集绑定 ID（必填） |
| `--model-binding-id` | int64 | `0` | 模型绑定 ID（必填） |
| `--image-id` | int64 | `0` | 镜像 ID（必填） |
| `--resource` | stringArray | — | 资源规格 `TYPE:NAME:NUMBER`（必填，可多次） |
| `--description` | string | — | 任务描述 |
| `--local-job-id` | string | `1` | 本地任务 ID |
| `--schedule-strategy` | string | `dataLocality` | 调度策略 |
| `--command` | string | — | 运行命令 |
| `--env` | stringArray | — | 环境变量 `KEY=VALUE`（可多次） |
| `--param` | stringArray | — | 运行参数 `KEY=VALUE`（可多次） |
| `--data` | string | — | 完整 JobSet JSON |
| `--data-file` | string | — | 完整 JobSet JSON 文件 |

```powershell
# 提交训练任务
jcc train submit `
  --name "train-task-001" `
  --cluster-id "cluster-01" `
  --code-binding-id 1 `
  --dataset-binding-id 2 `
  --model-binding-id 3 `
  --image-id 5 `
  --resource GPU:A100:1 `
  --command "python train.py"

# 多资源 + 环境变量
jcc train submit `
  --name "train-task-002" `
  --cluster-id "cluster-01" `
  --code-binding-id 1 --dataset-binding-id 2 --model-binding-id 3 --image-id 5 `
  --resource GPU:A100:2 --resource CPU:Xeon:8 `
  --env BATCH_SIZE=32 --env LEARNING_RATE=0.001 `
  --param epochs=100

# 通过文件提交
jcc train submit --data-file "./train-jobset.json"
```

### train query

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 任务名称过滤 |
| `--status` | string | — | 任务状态过滤 |
| `--page-num` | int64 | `0` | 页码 |
| `--page-size` | int64 | `0` | 页大小 |

```powershell
jcc train query
jcc train query --status Running --page-num 1 --page-size 20
```

### train detail

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-id` | string | — | 任务 ID（优先） |
| `--job-set-id` | string | — | JobSetID（与 `--local-job-id` 配合） |
| `--local-job-id` | string | — | 本地任务 ID（与 `--job-set-id` 配合） |

```powershell
jcc train detail --task-id "<task-id>"
jcc train detail --job-set-id "<job-set-id>" --local-job-id "1"
```

### train log

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-id` | string | — | 任务 ID（优先） |
| `--job-set-id` | string | — | JobSetID（与 `--local-job-id` 配合） |
| `--local-job-id` | string | — | 本地任务 ID（与 `--job-set-id` 配合） |

```powershell
jcc train log --task-id "<task-id>"
```

### train result

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-id` | string | — | 任务 ID |
| `--package-id` | int64 | `0` | 结果包 ID |
| `--package-name` | string | — | 结果包名称模糊匹配 |
| `--job-set-id` | string | — | JobSetID（与 `--local-job-id` 配合） |
| `--local-job-id` | string | — | 本地任务 ID（与 `--job-set-id` 配合） |

```powershell
jcc train result --task-id "<task-id>"
jcc train result --package-name "output"
```

### train resource-range

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--compute-type` | string | — | 计算类型 |
| `--image-tags` | string | — | 镜像标签，逗号分隔 |

```powershell
jcc train resource-range --compute-type "GPU" --image-tags "cuda12,pytorch"
```

### train resource-query

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--type` | string | — | 资源类型 GPU/NPU/CPU（必填） |
| `--cluster-id` | stringArray | — | 候选集群 ID（可多次） |
| `--cpu-min` | float64 | `0` | CPU 最小值 |
| `--cpu-max` | float64 | `0` | CPU 最大值 |
| `--gpu-min` | float64 | `0` | GPU 最小值 |
| `--gpu-max` | float64 | `0` | GPU 最大值 |
| `--gpu-number` | int | `0` | GPU 数量 |
| `--memory-min` | float64 | `0` | 内存最小值 (GB) |
| `--memory-max` | float64 | `0` | 内存最大值 (GB) |
| `--storage-min` | float64 | `0` | 存储最小值 (GB) |
| `--storage-max` | float64 | `0` | 存储最大值 (GB) |
| `--compute-type` | string | — | 计算类型 |
| `--image-tags` | string | — | 镜像标签，逗号分隔 |
| `--calc-network-cost` | bool | `false` | 是否计算网络成本 |

```powershell
jcc train resource-query --type GPU --gpu-number 1 --gpu-min 1 --gpu-max 8 --memory-min 16
```

---

## jcc inference —— 智算推理任务管理

构造 `type=PCM_Inference` 的 JobSet；taskID 优先。

### inference submit

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 任务名称（必填） |
| `--cluster-id` | string | — | 目标集群 ID（必填） |
| `--code-binding-id` | int64 | `0` | 代码绑定 ID（必填） |
| `--model-binding-id` | int64 | `0` | 模型绑定 ID（必填） |
| `--image-id` | int64 | `0` | 镜像 ID（必填） |
| `--binding-id` | int64 | `0` | 推理服务绑定 ID（必填） |
| `--resource` | stringArray | — | 资源规格 `TYPE:NAME:NUMBER`（必填，可多次） |
| `--description` | string | — | 任务描述 |
| `--local-job-id` | string | `1` | 本地任务 ID |
| `--schedule-strategy` | string | `dataLocality` | 调度策略 |
| `--command` | string | — | 运行命令 |
| `--env` | stringArray | — | 环境变量 `KEY=VALUE`（可多次） |
| `--param` | stringArray | — | 运行参数 `KEY=VALUE`（可多次） |
| `--data` | string | — | 完整 JobSet JSON |
| `--data-file` | string | — | 完整 JobSet JSON 文件 |

```powershell
# 提交推理任务
jcc inference submit `
  --name "infer-task-001" `
  --cluster-id "cluster-01" `
  --code-binding-id 1 --model-binding-id 2 --image-id 5 --binding-id 10 `
  --resource GPU:A100:1 `
  --command "python serve.py"

# 带环境变量
jcc inference submit `
  --name "infer-task-002" `
  --cluster-id "cluster-01" `
  --code-binding-id 1 --model-binding-id 2 --image-id 5 --binding-id 10 `
  --resource GPU:A100:4 `
  --env MODEL_PATH=/models/llama --env MAX_BATCH_SIZE=64 `
  --param port=8080

# 通过文件提交
jcc inference submit --data-file "./infer-jobset.json"
```

### inference query

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--name` | string | — | 任务名称过滤 |
| `--status` | string | — | 任务状态过滤 |
| `--page-num` | int64 | `0` | 页码 |
| `--page-size` | int64 | `0` | 页大小 |

```powershell
jcc inference query
jcc inference query --status Running
```

### inference detail

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-id` | string | — | 任务 ID（优先） |
| `--job-set-id` | string | — | JobSetID（与 `--local-job-id` 配合） |
| `--local-job-id` | string | — | 本地任务 ID（与 `--job-set-id` 配合） |

```powershell
jcc inference detail --task-id "<task-id>"
```

### inference log

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-id` | string | — | 任务 ID（优先） |
| `--job-set-id` | string | — | JobSetID（与 `--local-job-id` 配合） |
| `--local-job-id` | string | — | 本地任务 ID（与 `--job-set-id` 配合） |

```powershell
jcc inference log --task-id "<task-id>"
```

### inference stop

构造 `type=StopInference` 的 JobSet 停止推理任务。

| Flag | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--task-id` | string | — | 任务 ID（优先） |
| `--job-set-id` | string | — | JobSetID（与 `--local-job-id` 配合） |
| `--local-job-id` | string | — | 本地任务 ID（与 `--job-set-id` 配合） |

```powershell
jcc inference stop --task-id "<task-id>"
```

---

## 调试辅助

```powershell
# 干跑模式：只打印请求体，不发起网络请求
jcc hpc create --dry-run --name "test" --apptype vasp --cluster-name "HPCSlurmArm" --package-name "vasp-test"

# 详细模式：打印请求 URL 和原始响应
jcc cloud container query --verbose

# 纯文本输出（默认 JSON）
jcc train query --output text

# 查看任意命令帮助
jcc hpc create --help
jcc cloud container --help
```

---

## 环境变量速查

| 环境变量 | 对应参数 | 说明 |
| --- | --- | --- |
| `JCC_BASE_URL` | `--base-url` | 中间件根 URL |
| `JCC_JCC_URL` | `--jcc-url` | JCC 根 URL |
| `JCC_TOKEN` | `--token` | Authorization token |
| `JCC_USER_ID` | `--user-id` | 用户 ID |
| `JCC_DATASET_PACKAGE_ID` | `--package-id` | dataset schedule 默认包 ID |
| `JCC_MODEL_PACKAGE_ID` | `--package-id` | model schedule 默认包 ID |

---

## 代码入口

| 组件 | 路径 |
| --- | --- |
| 入口 main | `cmd/jcc/main.go` |
| 根命令 + login | `cliapp/run.go` |
| hpc | `internal/hpc/` |
| commonfile | `internal/commonfile/` |
| dataset / model | `internal/dataset/`、`internal/model/` → 复用 `internal/resource/` |
| cloud | `internal/cloud/` |
| jobset | `internal/jobset/` |
| train | `internal/train/` |
| inference | `internal/inference/` |
| 全局参数 | `internal/cliopts/` |
| HTTP 客户端 | `internal/mwhttp/` |

> 最后更新：2026-06-15

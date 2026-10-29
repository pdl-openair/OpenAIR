# 数据上传任务步骤

## 概述

本文件定义上传数据到云际平台的执行步骤。数据上传通过数据集、模型或文件包的创建命令实现。

---

## 执行步骤

### 阶段1：准备工作

#### 步骤1.1：检查登录状态

```bash
$login = jcc login --username <username> --password <password> | ConvertFrom-Json
$env:JCC_TOKEN = $login.token
$env:JCC_USER_ID = $login.userID
```

#### 步骤1.2：检查本地文件

```bash
# 确认本地文件/目录存在
Test-Path -Path <local_path>
```

**预期输出**:
- `True`: 路径存在
- `False`: 路径不存在，需要创建或修正路径

#### 步骤1.3：获取可选集群列表

通过查询已有资源或平台接口，获取可选集群列表：

```bash
# 查询已有资源，了解可用集群
jcc dataset query --output text
jcc model query --output text
jcc commonfile query --output text

# 或调用平台集群查询能力
jcc cluster query --output text
```

**说明**: 从返回结果中提取集群 ID 列表，作为后续选择依据。

#### 步骤1.4：选择目标集群

基于返回的集群列表选择目标集群，并记录集群 ID：

```bash
$selectedCluster = "<cluster-id>"
```

**说明**: `<cluster-id>` 为从上述列表中选择的实际集群标识。

### 阶段2：执行上传

#### 步骤2.1：上传数据集

```bash
jcc dataset create \
  --name <dataset-name> \
  --path <local-path> \
  [--clusters <cluster-id> ...]
```

**参数说明**:
- `--name`: 数据集名称（必填）
- `--path`: 本地数据路径（必填），可以是文件或目录
- `--clusters`: 目标集群ID（可选，可指定多个）

**示例**:
```bash
jcc dataset create --name cifar10-dataset --path ./data/cifar10/ --clusters ai-cluster-01
```

#### 步骤2.2：上传模型

```bash
jcc model create \
  --name <model-name> \
  --path <local-path> \
  [--clusters <cluster-id> ...]
```

**参数说明**:
- `--name`: 模型名称（必填）
- `--path`: 本地模型路径（必填），包含模型文件的目录
- `--clusters`: 目标集群ID（可选，可指定多个）

**示例**:
```bash
jcc model create --name llama2-7b --path ./models/llama2-7b/ --clusters ai-cluster-01
```

#### 步骤2.3：上传HPC文件包

```bash
jcc commonfile create \
  --name <package-name> \
  --path <local-path> \
  --cluster-name <cluster-name>
```

**参数说明**:
- `--name`: 文件包名称（必填）
- `--path`: 本地文件路径（必填），包含所有文件的目录
- `--cluster-name`: 目标集群名称（必填）

**示例**:
```bash
jcc commonfile create --name my-data-package --path ./my-files/ --cluster-name HPCSlurmArm
```

### 阶段3：验证上传结果

#### 步骤3.1：查看上传后的资源

```bash
# 查询数据集
jcc dataset query --output text

# 查询模型
jcc model query --output text

# 查询文件包
jcc commonfile query --output text
```

**输出示例**:
```
ID              Name              Status     Size
-----           ----              ------     ----
dataset-001     cifar10-dataset   ready      1.2GB
```

**状态说明**:
| 状态 | 说明 |
|------|------|
| `uploading` | 上传中 |
| `ready` | 准备就绪 |
| `failed` | 上传失败 |

---

## 执行示例

```bash
# 登录
$login = jcc login --username admin --password mypass123 | ConvertFrom-Json
$env:JCC_TOKEN = $login.token
$env:JCC_USER_ID = $login.userID

# 上传数据集
jcc dataset create --name my-dataset --path ./data/training/ --clusters ai-cluster-01

# 查看上传结果
jcc dataset query --output text
```
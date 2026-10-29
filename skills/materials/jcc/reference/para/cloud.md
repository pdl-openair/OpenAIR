# 云资源任务参数

## 概述

本文件定义云资源管理任务（容器/虚拟机实例）所需的参数。

---

## 容器管理参数

### 创建容器

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | string | 是 | 容器名称 |
| image | string | 是 | 镜像 |
| cpu | string | 是 | CPU资源 |
| memory | string | 是 | 内存资源 |
| port | int32 | 否 | 端口 |
| replica | int32 | 否 | 副本数（默认1） |
| command | string | 否 | 启动命令 |
| args | string[] | 否 | 启动参数（可多次） |
| env | string[] | 否 | 环境变量 KEY=VALUE（可多次） |
| description | string | 否 | 描述 |
| mount_path | string | 否 | 挂载路径 |
| node_port | int32 | 否 | 节点端口 |
| container_group | string | 否 | 容器组名称（默认同name） |
| capacity | int32 | 否 | 容量 |

#### CLI命令

```bash
# 创建容器
jcc cloudcontainer create \
  --cluster-name <cluster-name> \
  --name <name> \
  --image <image> \
  --cpu <cpu> \
  --memory <memory> \
  [--port <port>] \
  [--replica <replica>]
```

### 查询容器集群列表

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| type | string | 否 | 资源类型（CPU/GPU），默认 CPU |

#### CLI命令

```bash
# 查询所有容器集群
jcc cloudcontainer cluster-list

# 按资源类型筛选
jcc cloudcontainer cluster-list --type GPU
```

### 查询容器

#### 参数列表

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| name | string | 否 | - | 容器名称（不传则查询列表，指定后查询详情） |
| id | string | 否 | - | 容器ID（兼容旧用法） |
| status | string | 否 | - | 任务状态过滤 |
| page_num | int64 | 查询列表时必填 | 1 | 页码 |
| page_size | int64 | 查询列表时必填 | 10 | 页大小 |

#### CLI命令

```bash
# 查询所有容器（需传分页参数）
jcc cloudcontainer query --page-num 1 --page-size 10

# 查询指定容器
jcc cloudcontainer query --name <container-name>

# 按状态过滤（需传分页参数）
jcc cloudcontainer query --status running --page-num 1 --page-size 10

# 分页查询
jcc cloudcontainer query --page-num 1 --page-size 10
```

---

### 容器日志

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | string | 是 | 容器名称 |

#### CLI命令

```bash
jcc cloudcontainer log --name <container-name>
```

> 注意：需要在容器状态变为 Running 后才能查询日志，Pending 状态下 deployment 未创建完成会导致 deployments.apps "xxx" not found 错误

---

### 删除容器

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | string | 是 | 容器名称 |

#### CLI命令

```bash
jcc cloudcontainer delete --name <container-name>
```

---

## 云算虚拟机实例管理参数

### 查询虚拟机实例集群列表

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| type | string | 否 | 资源类型（CPU/GPU），默认 CPU |

#### CLI命令

```bash
# 查询所有虚拟机实例集群
jcc cloudinstance cluster-list

# 按资源类型筛选
jcc cloudinstance cluster-list --type GPU
```

### 查询虚拟机实例

#### 参数列表

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| host_name | string | 否 | - | 主机名（不带分页参数时查询详情，带分页参数时过滤列表） |
| status | string | 否 | - | 状态筛选（running/stopped/pending等） |
| page_num | int64 | 否 | 1 | 页码 |
| page_size | int64 | 否 | 10 | 页大小 |

#### CLI命令

```bash
# 查询所有虚拟机实例
jcc cloudinstance query --output text

# 查询指定虚拟机实例详情（不带分页参数）
jcc cloudinstance query --host-name <host-name> --output text

# 按主机名筛选列表（带分页参数）
jcc cloudinstance query --host-name <host-name> --page-num 1 --page-size 10 --output text

# 按状态筛选
jcc cloudinstance query --status <status> --output text

# 组合筛选
jcc cloudinstance query --host-name <host> --status <status> --output text
```

#### 返回字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | string | 虚拟机实例ID |
| hostName | string | 主机名 |
| status | string | 虚拟机实例状态 |
| cpu | string | CPU配置 |
| memory | string | 内存配置 |
| gpu | string | GPU配置（如有） |
| privateIp | string | 私网IP地址 |
| publicIp | string | 公网IP地址 |
| createdAt | string | 创建时间 |
| updatedAt | string | 更新时间 |

#### 输出示例

```json
{
  "id": "instance-001",
  "hostName": "vm-node-01",
  "status": "running",
  "resource": {
    "cpu": "8 cores",
    "memory": "32 GB",
    "gpu": "A100:1"
  },
  "network": {
    "privateIp": "192.168.1.100",
    "publicIp": "203.0.113.50"
  },
  "createdAt": "2024-01-15T08:00:00Z"
}
```

---

### 操作虚拟机实例

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| host_name | string | 是 | 主机名 |
| operate_type | string | 是 | 操作类型 |

#### operate_type 可选值

| 操作类型 | 说明 | 适用状态 |
|----------|------|----------|
| `start` | 启动虚拟机实例 | stopped, pending |
| `stop` | 停止虚拟机实例 | running |
| `delete` | 删除虚拟机实例 | running, stopped |

#### CLI命令

```bash
# 启动虚拟机实例
jcc cloudinstance operate --host-name <host-name> --operate-type start

# 停止虚拟机实例
jcc cloudinstance operate --host-name <host-name> --operate-type stop

# 删除虚拟机实例
jcc cloudinstance operate --host-name <host-name> --operate-type delete
```

#### 返回字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | string | 虚拟机实例ID |
| previousStatus | string | 操作前状态 |
| currentStatus | string | 操作后状态 |
| message | string | 操作结果消息 |

#### 输出示例

```json
{
  "id": "instance-001",
  "previousStatus": "stopped",
  "currentStatus": "pending",
  "message": "Instance start operation submitted successfully"
}
```

---

### 创建虚拟机实例

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| cluster_name | string | 是/二选一 | 集群名称（与cluster-id二选一） |
| cluster_id | string | 是/二选一 | 集群ID（与cluster-name二选一） |
| host_name | string | 是 | 主机名。主机名必须唯一，重复的主机名会导致创建失败（状态变为 Deleted 或查询不到），建议使用随机/唯一的主机名 |
| instance_type | string | 是 | 虚拟机实例类型，即sourceKey（来自cloudinstance cluster-list返回的SOURCE KEY列） |
| password | string | 是 | 登录密码 |
| image_id | string | 是 | 镜像标识，即External ID（来自image list返回的External ID列） |
| system_disk_size | string | 是 | 系统盘大小(GB) |
| username | string | 否 | 用户名（默认 root） |
| charge_type | string | 否 | 计费类型（默认 PostPaid） |
| disk_category | string | 否 | 系统盘类型（默认 cloud_ssd） |
| disk_level | string | 否 | 性能级别（默认 PL0） |
| replica | int32 | 否 | 副本数（默认 1） |
| security_group_id | string | 否 | 安全组ID |

#### 镜像选择流程

在选择资源规格后，使用flavor-id查询匹配的镜像列表：

```bash
jcc image list --flavor-id <flavor-id> --output text
```

**参数说明**:
- `--flavor-id`: 资源规格ID（来自cloudinstance cluster-list返回的ID列）
- `--page`: 分页页码（默认1）
- `--size`: 每页数量（默认10）

**输出示例**:
```
total: 3  page: 1  size: 10

=== 镜像列表 ===
ID   External ID                                     Name                                                  Tag  Kind  Provider  Status     Description                                     Created
658  ubuntu_20_04_uefi_x64_20G_alibase_20220324.vhd  ubuntu_20_04_uefi_x64_20G_alibase_20220324.vhd       vm    阿里云       available                                                  2026-02-06 02:22:23
654  ubuntu_18_04_uefi_x64_20G_alibase_20220330.vhd  ubuntu_18_04_uefi_x64_20G_alibase_20220330.vhd       vm    阿里云       available                                                  2026-02-06 02:22:23
297  ubuntu_20_04_x64_20G_alibase_20251031.vhd       ubuntu_20_04_x64_20G_alibase_20251031.vhd            vm    阿里云       available  Kernel version is 5.4.0-216-generic, 2025.11.3  2026-02-06 02:22:23
```
其中 `External ID` 为底层云平台的镜像标识（如阿里云镜像文件名），`Name` 为平台可见的镜像名称。
**创建命令中的 `--image-id` 应使用 `External ID` 列的值**。

#### CLI命令

```bash
jcc cloudinstance create \
  --cluster-name <cluster-name> \
  --host-name <host-name> \
  --instance-type <sourceKey> \
  --password <password> \
  --image-id <externalImageId> \
  --system-disk-size <size> \
  [--cluster-id <cluster-id>] \
  [--username <username>] \
  [--charge-type <charge-type>] \
  [--disk-category <disk-category>] \
  [--disk-level <disk-level>] \
  [--replica <replica>] \
  [--security-group-id <security-group-id>]
```

**示例**:
```bash
jcc cloudinstance create --cluster-name "阿里云" --host-name node-1 --instance-type ecs.t5-lc2m1.nano --password MyPass123 --image-id ubuntu_20_04_x64_20G_alibase_20251031.vhd --system-disk-size 20 --username root --charge-type PostPaid --disk-category cloud_ssd --disk-level PL0 --replica 1
```

#### 返回字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| jobSetId | string | 任务集ID |
| message | string | 创建结果消息 |

---

### 资源配置规格参考

| 虚拟机实例类型 | CPU | 内存 | 适用场景 |
|----------|-----|------|----------|
| ecs.t5-lc2m1.nano | 1核 | 0.5GB | 轻量级测试 |
| ecs.t5-lc2m2.small | 1核 | 2GB | 基础应用 |
| ecs.t5-lc4m2.medium | 2核 | 4GB | 通用计算 |
| ecs.t5-lc8m4.large | 4核 | 8GB | 高性能计算 |

---

### 计费类型说明

| 类型 | 说明 |
|------|------|
| PostPaid | 按量付费 |
| PrePaid | 包年包月 |

---

## 常见问题

### Q1: 如何获取虚拟机实例信息？

```bash
# 查询所有虚拟机实例
jcc cloudinstance query --output text
```

### Q2: 操作失败，显示 "Operation not allowed"？

检查虚拟机实例当前状态：
- `start` 操作仅适用于 `stopped` 状态
- `stop` 操作仅适用于 `running` 状态

### Q3: 如何查看虚拟机实例的详细配置？

```bash
jcc cloudinstance query --host-name <host-name> --output text
```

### Q4: 虚拟机实例无法启动怎么办？

1. 检查资源配额是否充足
2. 检查安全组配置
3. 查看虚拟机实例日志排查问题
4. 联系管理员支持

# 算法管理参数

## 概述

本文件定义算法管理任务所需的参数。

---

## 创建算法包

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | string | 是 | 算法名称 |
| path | string | 是 | 本地文件或目录路径 |

### CLI命令

```bash
jcc algo create \
  --name <name> \
  --path <path>
```

**示例**:
```bash
jcc algo create --name my-algo --path ./code
```

---

## 创建子算法

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| parent_name | string | 是 | 父算法包名称 |
| cluster_name | string | 是 | 目标集群名称 |
| description | string | 是 | 子算法名称 |

### CLI命令

```bash
jcc algo fork \
  --parent-name <parent-name> \
  --cluster-name <cluster-name> \
  --description <description>
```

**示例**:
```bash
jcc algo fork --parent-name my-algo --cluster-name HPCSlurmArm --description my-algo-v2
```

---

## 查询算法包

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| package_name | string | 否 | 算法包名称（不传则查询列表） |

### CLI命令

```bash
# 查询所有算法包
jcc algo query

# 查询指定算法包详情
jcc algo query --package-name <name>
```

---

## 查看算法文件内容

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| package_name | string | 是 | 父算法包名称 |
| description | string | 否 | 子算法名称（不传则查看父算法） |

### CLI命令

```bash
# 查看父算法文件
jcc algo view --package-name <name>

# 查看子算法文件
jcc algo view --package-name <name> --description <description>
```

---

## 删除算法包

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| package_name | string | 是 | 算法包名称 |
| force | bool | 否 | 跳过确认提示，直接执行删除 |

### CLI命令

```bash
# 删除算法包（需确认）
jcc algo delete --package-name <name>

# 强制删除（跳过确认）
jcc algo delete --package-name <name> --force
```

---

## 查询可创建算法的集群列表

### 参数列表

无

### CLI命令

```bash
jcc algo cluster-list
```
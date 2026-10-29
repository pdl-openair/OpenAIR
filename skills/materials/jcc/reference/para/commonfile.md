# 个人文件管理参数

## 概述

本文件定义个人文件管理任务所需的参数。

## 创建文件包

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | string | 是 | 文件包名称 |
| path | string | 是 | 本地文件路径 |
| cluster_name | string | 是 | 目标集群名称 |

### CLI命令

```bash
jcc commonfile create --name <name> --path <path> --cluster-name <cluster>
```

---

## 查询文件包

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| package_name | string | 否 | 文件包名称（不传则查询列表） |

### CLI命令

```bash
# 查询所有文件包
jcc commonfile query

# 查询指定文件包
jcc commonfile query --package-name <package-name>
```

---

## 调度文件包

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| cluster_name | string | 是 | 目标集群名称 |
| package_name | string | 否 | 文件包名称 |

### CLI命令

```bash
jcc commonfile schedule --cluster-name <cluster-name> [--package-name <package-name>]
```

---

## 查询文件包集群列表

### CLI命令

```bash
jcc commonfile cluster-list
```

---

## 删除文件包

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| package_name | string | 是 | 文件包名称 |

### CLI命令

```bash
jcc commonfile delete --package-name <package-name>
```

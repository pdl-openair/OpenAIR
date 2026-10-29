# 数据集管理参数

## 概述

本文件定义数据集管理任务所需的参数。

## 创建数据集

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | string | 是 | 数据集名称 |
| path | string | 是 | 本地文件路径 |
| clusters | string[] | 否 | 目标集群名称列表 |

### CLI命令

```bash
jcc dataset create --name <name> --path <path> [--clusters <cluster-name> ...]
```

---

## 查询数据集

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| package_name | string | 否 | 数据集包名称（不传则查询列表） |

### CLI命令

```bash
# 查询所有数据集
jcc dataset query

# 查询指定数据集
jcc dataset query --package-name <dataset-name>
```

---

## 调度数据集

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| cluster_name | string | 是 | 目标集群名称 |
| package_name | string | 否 | 数据集名称 |

### CLI命令

```bash
jcc dataset schedule --cluster-name <cluster-name> [--package-name <dataset-name>]
```

---

## 查询数据集集群列表

### CLI命令

```bash
jcc dataset cluster-list
```

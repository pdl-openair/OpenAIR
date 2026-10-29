# 模型管理参数

## 概述

本文件定义模型管理任务所需的参数。

## 创建模型

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| name | string | 是 | 模型名称 |
| path | string | 是 | 本地文件路径 |
| clusters | string[] | 否 | 目标集群名称列表 |

### CLI命令

```bash
jcc model create --name <name> --path <path> [--clusters <cluster-name> ...]
```

---

## 查询模型

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| package_name | string | 否 | 模型包名称（不传则查询列表） |

### CLI命令

```bash
# 查询所有模型
jcc model query

# 查询指定模型
jcc model query --package-name <model-name>
```

---

## 调度模型

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| cluster_name | string | 是 | 目标集群名称 |
| package_name | string | 否 | 模型名称 |

### CLI命令

```bash
jcc model schedule --cluster-name <cluster-name> [--package-name <model-name>]
```

---

## 查询模型集群列表

### CLI命令

```bash
jcc model cluster-list
```

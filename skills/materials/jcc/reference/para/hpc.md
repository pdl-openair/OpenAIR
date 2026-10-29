# 超算任务参数

## 概述

本文件定义提交超算任务所需的参数。

## 参数列表

| 参数名 | 类型 | 必填 | 说明 | 默认值 |
|--------|------|------|------|--------|
| name | string | 是 | 任务名称 | - |
| cluster_name | string | 是 | 目标集群名称 | - |
| package_name | string | 是 | 已有包名称 | - |
| apptype | string | 是 | 应用类型（如 vasp、lammps） | - |
| file_name | string | 条件必填 | 包内文件名（非 VASP 应用必填） | - |
| nodes | string | 否 | 计算节点数量 | "1" |
| ntasks | string | 否 | 每个节点任务数 | "4" |
| param | stringArray | 否 | 运行参数 KEY=VALUE（可多次） | - |

## 参数说明

### name
- 类型：字符串
- 说明：任务名称，用于标识和管理
- 示例："vasp-task-001"

### cluster_name
- 类型：字符串
- 说明：目标集群名称
- 示例："HPCSlurmArm"

### package_name
- 类型：字符串
- 说明：已存在的HPC文件包名称
- 示例："vasp-test0604"

### apptype
- 类型：字符串
- 说明：应用类型
- 示例："vasp"、"lammps"

### file_name
- 类型：字符串
- 说明：包内文件名（非 VASP 应用必填）；VASP 流程会自动校验 POSCAR/INCAR/KPOINTS/POTCAR，无需此参数
- 示例："in.min"

### nodes
- 类型：字符串（数字）
- 说明：计算节点数量
- 默认值："1"

### ntasks
- 类型：字符串（数字）
- 说明：每个节点任务数
- 默认值："4"

### param
- 类型：stringArray
- 说明：运行参数 KEY=VALUE（可多次）
- 示例："--param KEY=VALUE"

## 输入示例

```json
{
  "name": "vasp-task",
  "cluster_name": "HPCSlurmArm",
  "package_name": "vasp-test0604",
  "apptype": "vasp",
  "nodes": "1",
  "ntasks": "4"
}
```

## CLI命令

```bash
# VASP 应用
jcc hpc create \
  --name vasp-task \
  --cluster-name HPCSlurmArm \
  --package-name vasp-test0604 \
  --apptype vasp \
  --nodes 1 \
  --ntasks 4

# 非 VASP 应用（如 lammps，需指定 --file-name）
jcc hpc create \
  --name lammps-task \
  --cluster-name HPCSlurmArm \
  --package-name lammps-pkg \
  --apptype lammps \
  --file-name in.min
```

---

## 查询集群列表

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| apptype | string | 是 | 应用类型（如 vasp、lammps） |

### CLI命令

```bash
jcc hpc cluster-list --apptype vasp
jcc hpc cluster-list --apptype lammps
```
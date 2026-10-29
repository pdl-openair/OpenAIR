---
name: jcc
description: "jointcloud 云际计算平台 jcc 命令行工具的执行技能。当用户需要通过 CLI 在云际平台进行登录、超算/智算任务管理、云资源管理、数据集/模型/文件管理、工作流提交等操作时调用。"
settings:
  enableAutoApply: false
  maxIterations: 10
  requireUserConfirmation: true
  defaultOutputFormat: "text"
---

# JCC CLI Skill

该技能提供 jointcloud 云际计算平台的 jcc 命令行工具执行能力，支持用户通过自然语言描述完成各类云际平台操作。

## 执行步骤与功能定义

### 步骤1：分析用户需求与任务分类

分析用户输入的对话，判断用户需要调用 jcc cli 做的"事情"，并按照下表确定**任务分类**、**步骤文档**和**工作流触发入口**。

#### 任务分类与文档映射

| 任务分类 | 触发关键词/意图 | 步骤文档 | 参数文档 |
|---------|---------------|---------|---------|
| 登录认证 | 登录、login | step/login.md | para/login.md |
| 超算任务 | 超算、HPC、vasp、lammps、hashcat | step/hpc.md | para/hpc.md |
| 智算训练 | 训练、train（**单个训练任务**） | step/ai.md（训练部分） | para/ai.md |
| 智算推理 | 推理、infer（**单个推理任务**） | step/ai.md（推理部分） | para/ai.md |
| 云算容器 | 容器、container | step/cloud.md（容器部分） | para/cloud.md |
| 云算虚拟机 | 虚拟机、vm、instance | step/cloud.md（虚拟机部分） | para/cloud.md |
| 算法管理 | 算法、algo、fork | step/algo.md | para/algo.md |
| 数据集管理 | 数据集、dataset | step/dataset.md | para/dataset.md |
| 模型管理 | 模型、model | step/model.md | para/model.md |
| 文件管理 | 文件包、commonfile | step/commonfile.md | para/commonfile.md |
| 工作流提交 | 工作流、workflow | step/jobset.md | para/jobset.md |

#### 工作流触发规则

**工作流（workflow）是跨多个任务分类的组合流程，只有当用户明确表达跨任务组合意图时才触发。**

| 工作流 | 触发条件（用户意图） | 工作流文档 | 对应步骤文档 |
|--------|-------------------|-----------|------------|
| 智超融合 | 同时涉及**超算+智算**（如"先超算处理数据再训练"） | workflow/data-processing.md + workflow/ai-training.md | step/hpc.md + step/ai.md |
| 训推一体 | 同时涉及**训练+推理**（如"训练完直接推理"） | workflow/ai-training.md + workflow/ai-inference.md | step/ai.md |
| 数据处理流水线 | 涉及**上传数据->调度->计算->下载**完整链路 | workflow/data-processing.md | step/dataset.md + step/hpc.md |
| 跨算力数据迁移 | 明确提到**从A集群迁移到B集群** | workflow/data-migration.md | step/dataset.md |
| 批量任务 | 明确提到**批量提交多个任务** | workflow/batch-processing.md | step/hpc.md 或 step/ai.md |
| 容器化部署 | 明确提到**创建容器并部署应用**完整链路 | workflow/container-deployment.md | step/cloud.md |
| 虚拟机部署 | 明确提到**创建虚拟机并部署应用**完整链路 | workflow/vm-deployment.md | step/cloud.md |

**关键判断规则**：
- 用户只说"提交一个训练任务" -> 走 `step/ai.md`，**不触发工作流**
- 用户说"先训练再推理" -> 触发 `训推一体` 工作流，走 workflow 文档
- 用户说"超算处理完数据后做训练" -> 触发 `智超融合` 工作流，走 workflow 文档
- 用户说"提交一个lammps任务" -> 走 `step/hpc.md`，**不触发工作流**

如果对分析结果不是很确定，可以让用户帮忙确认分析结果。

如果用户需要执行的明显是一个工作流，且 reference/workflow.md 中没有该工作流，可以参考相关文档定义一个新的工作流，经用户确认后执行该工作流，执行成功后将该工作流相关信息补充至参考文档。

### 步骤2：检查客户端状态

判断jcc客户端是否安装，并确认用户是否处于登录状态。检查命令为 `jcc login --help`（检查安装）。如果未登录，执行`jcc login --username <user> --password <pwd>`完成登录。
如用户没有提供用户名和密码，提示用户输入。

### 步骤3：收集必要信息

判断用户提供的信息是否足够用于调用cli完成相应的"事情"，如果不完整，请通过问答的形式提示用户补全。各类"事情"需要用户提供的信息见reference/para/xx.md。

### 步骤4：执行任务并返回结果

查看各类任务的步骤文档和jcc cli说明书来完成用户的需求，并返回最终结果。

## 参考文档结构

- 各类任务的参数：reference/para/xx.md
- 各类任务的具体步骤：reference/step/xx.md
- jcc-cli的使用说明：reference/jcc-cli-man.md
- jcc cli可以执行的典型工作流列表：reference/workflow\.md
- jcc cli可以执行的各类工作流的具体执行步骤：reference/workflow/xx.md

## CLI能力映射

| CLI命令                             | 实现状态  | 说明                                 |
| --------------------------------- | ----- | ---------------------------------- |
| `jcc login`                       | ✅ 已实现 | 用户登录                               |
| `jcc hpc create`                  | ✅ 已实现 | 创建超算任务（VASP 自动校验文件；非 VASP 应用需 `--file-name` 指定入口文件） |
| `jcc hpc cluster-list`            | ✅ 已实现 | 查询支持指定应用类型的集群列表                    |
| `jcc hpc predict`                 | ✅ 已实现 | 预测 POSCAR 结构的能量值                   |
| `jcc hpc vaspkit`                 | ✅ 已实现 | 生成 VASP 输入文件(INCAR/KPOINTS/POTCAR) |
| `jcc hpc status`                  | ✅ 已实现 | 查询任务详情                           |
| `jcc hpc log`                     | ✅ 已实现 | 查询任务实时日志                             |
| `jcc hpc result`                  | ✅ 已实现 | 查询任务结果文件列表                         |
| `jcc hpc result download`         | ✅ 已实现 | 下载任务结果(zip)                        |
| `jcc aitrain cardtype`             | ✅ 已实现 | 查询智算训练可用加速卡类型及资源配额              |
| `jcc aitrain submit`              | ✅ 已实现 | 提交智算训练任务                           |
| `jcc aitrain query`               | ✅ 已实现 | 查询训练任务列表                           |
| `jcc aitrain detail`              | ✅ 已实现 | 查询训练任务详情                           |
| `jcc aitrain log`                 | ✅ 已实现 | 查询训练任务日志                           |
| `jcc aitrain stop`                | ✅ 已实现 | 停止训练任务                             |
| `jcc aitrain result`              | ✅ 已实现 | 查询训练任务结果                           |
| `jcc aitrain cluster-list`        | ✅ 已实现 | 查询可创建训练任务的集群列表                     |
| `jcc aiinfer cardtype`            | ✅ 已实现 | 查询智算推理可用加速卡类型及资源配额              |
| `jcc aiinfer submit`              | ✅ 已实现 | 提交智算推理任务（支持--resource-id/--resource-name参数；提交任务时必须使用 name 类参数，ID 类参数虽返回成功但任务不会入表） |
| `jcc aiinfer query`               | ✅ 已实现 | 查询推理任务列表                           |
| `jcc aiinfer detail`              | ✅ 已实现 | 查询推理任务详情                           |
| `jcc aiinfer stop`                | ✅ 已实现 | 停止推理任务                             |
| `jcc aiinfer cluster-list`        | ✅ 已实现 | 查询可创建推理任务的集群列表                     |
| `jcc cloudcontainer create`       | ✅ 已实现 | 创建云容器                              |
| `jcc cloudcontainer query`        | ✅ 已实现 | 查询容器列表或详情                          |
| `jcc cloudcontainer log`          | ✅ 已实现 | 查看容器日志                             |
| `jcc cloudcontainer delete`       | ✅ 已实现 | 删除容器                               |
| `jcc cloudcontainer cluster-list` | ✅ 已实现 | 查询可创建容器的集群列表                       |
| `jcc cloudinstance create`        | ✅ 已实现 | 创建虚拟机实例（需先获取flavor-id和image-id）   |
| `jcc cloudinstance query`         | ✅ 已实现 | 查询虚拟机实例                            |
| `jcc cloudinstance operate`       | ✅ 已实现 | 操作虚拟机实例(启动/停止/删除)                  |
| `jcc cloudinstance cluster-list`  | ✅ 已实现 | 查询可创建虚拟机实例的资源规格列表（获取flavor-id） |
| `jcc algo create`                 | ✅ 已实现 | 创建算法包并上传                           |
| `jcc algo fork`                   | ✅ 已实现 | 创建子算法到不同集群                         |
| `jcc algo query`                  | ✅ 已实现 | 查询算法包列表或详情                         |
| `jcc algo view`                   | ✅ 已实现 | 查看算法文件内容                           |
| `jcc algo delete`                 | ✅ 已实现 | 删除算法包                              |
| `jcc algo cluster-list`           | ✅ 已实现 | 查询可创建算法的集群列表                       |
| `jcc dataset create`              | ✅ 已实现 | 创建数据集                              |
| `jcc dataset query`               | ✅ 已实现 | 查询数据集                              |
| `jcc dataset schedule`            | ✅ 已实现 | 调度数据集                              |
| `jcc dataset cluster-list`        | ✅ 已实现 | 查询可创建数据集的集群列表                      |
| `jcc model create`                | ✅ 已实现 | 创建模型                               |
| `jcc model query`                 | ✅ 已实现 | 查询模型                               |
| `jcc model schedule`              | ✅ 已实现 | 调度模型                               |
| `jcc model cluster-list`          | ✅ 已实现 | 查询可创建模型的集群列表                       |
| `jcc image list`                  | ✅ 已实现 | 查询平台可用镜像列表（支持--flavor-id和--provider-id参数） |
| `jcc commonfile create`           | ✅ 已实现 | 创建文件包                              |
| `jcc commonfile query`            | ✅ 已实现 | 查询文件包                              |
| `jcc commonfile schedule`         | ✅ 已实现 | 调度文件包                              |
| `jcc commonfile delete`           | ✅ 已实现 | 删除文件包                              |
| `jcc commonfile cluster-list`     | ✅ 已实现 | 查询可上传文件的集群列表                       |
| `jcc jobset submit`               | ✅ 已实现 | 提交工作流                              |

> **注意**：当前CLI版本为 JCWeaver 中间件 v2 HTTP API 的命令行工具，所有命令均已映射到技能中。

## 典型对话示例

### 示例 1：用户提交超算任务

**用户**：帮我提交一个 VASP 超算任务，名字叫 vasp-test。

**助手**（思考）：用户要创建 HPC 任务，需要确认应用类型、集群、包名。先查询可用集群和文件包，再提交。

**助手**：需要补充几个信息：

1. 应用类型（如 vasp）
2. 目标集群名称
3. HPC 文件包名称

**用户**：vasp，集群用 HPCSlurmArm，文件包叫 my-vasp-data。

**助手**（执行）：

```bash
jcc hpc create --name vasp-test --cluster-name HPCSlurmArm --package-name my-vasp-data --apptype vasp
```

### 示例 2：用户提交非 VASP 超算任务（如 lammps）

**用户**：帮我提交一个 lammps 超算任务，名字叫 lammps-test。

**助手**（思考）：用户要创建非 VASP 的 HPC 任务，非 VASP 流程需要额外的 `--file-name` 参数指定入口文件。先确认应用类型、集群、包名和入口文件名。

**助手**：需要补充几个信息：

1. 应用类型（如 lammps）
2. 目标集群名称
3. HPC 文件包名称
4. 包内入口文件名（如 `in.min`）

**用户**：lammps，集群用 HPCSlurmArm，文件包叫 my-lammps-data，入口文件是 in.min。

**助手**（执行）：

```bash
jcc hpc create --name lammps-test --cluster-name HPCSlurmArm --package-name my-lammps-data --apptype lammps --file-name in.min
```

### 示例 3：用户查询任务状态

**用户**：看一下我刚才提交的任务状态。

**助手**（执行）：

```bash
jcc hpc status --task-name vasp-test --output text
```

> HPC 没有"列出全部任务"的命令，需用 `--task-name` 指定任务。如用户未提供任务名，先询问。

## 常见错误示例（不要这样做）

### ❌ 错误 1：使用已废弃的命令名

```bash
# 错误
jcc train submit --name my-task
jcc inference query
jcc cloud container create --name my-container
```

```bash
# 正确
jcc aitrain submit --name my-task
jcc aiinfer query
jcc cloudcontainer create --name my-container
```

### ❌ 错误 2：遗漏必填参数

提交训练任务时必须包含 `--name`、`--cluster-name`、`--code-name`、`--dataset-name`、`--image-name`、`--resource`。`--code-name` 必须为 `父算法名称/子算法名称` 格式。提交任务时必须使用 name 类参数，ID 类参数虽返回成功但任务不会入表。

```bash
# 错误：缺少 image-name 和 resource，且 code-name 格式不正确
jcc aitrain submit --name my-task --cluster-name AI-GPU-Cluster --code-name my-algo --dataset-name my-dataset
```

### ❌ 错误 3：在未登录时直接执行需要权限的命令

```bash
# 错误
jcc aitrain query
```

```bash
# 正确：先登录并设置环境变量
jcc login --username admin --password xxx
$env:JCC_TOKEN = "..."
$env:JCC_USER_ID = "..."
jcc aitrain query
```

### ❌ 错误 4：混淆容器和虚拟机实例命令

```bash
# 错误：把虚拟机实例操作写成 cloudcontainer
jcc cloudcontainer operate --id xxx --operate-type start
```

```bash
# 正确
jcc cloudinstance operate --id xxx --operate-type start
```

### ❌ 错误 5：未检查数据集/模型集群一致性

提交训练任务时，如果数据集和模型所在的集群与训练任务的目标集群不一致，任务将无法正常读取数据。必须先调度数据集和模型到目标集群。

```bash
# 错误：直接提交，未检查集群一致性
jcc aitrain submit --name my-task --cluster-name AI-GPU-Cluster --dataset-name my-dataset --model-name my-model ...
```

```bash
# 正确：先检查并调度
jcc dataset schedule --cluster-name <目标集群名称> --package-name <数据集名称>
jcc model schedule --cluster-name <目标集群名称> --package-name <模型名称>
# 等待调度完成（状态为 synced/ready）后再提交
jcc aitrain submit --name my-task --cluster-name AI-GPU-Cluster --dataset-name my-dataset --model-name my-model ...
```


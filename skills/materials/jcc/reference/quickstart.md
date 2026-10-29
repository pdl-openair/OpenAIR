# 云际计算 Skill 快速入门指南

欢迎使用云际计算 Skill！本指南将帮助你快速上手使用 jcc CLI 工具进行云际平台操作。

---

## 目录

1. [环境准备](#1-环境准备)
2. [登录认证](#2-登录认证)
3. [快速上手示例](#3-快速上手示例)
   - 示例1：提交超算任务
   - 示例2：提交智算训练任务
   - 示例3：管理云容器
4. [常用命令速查](#4-常用命令速查)
5. [环境变量配置](#5-环境变量配置)
6. [常见问题](#6-常见问题)

---

## 1. 环境准备

### 1.1 检查 jcc CLI 是否安装

打开命令行终端，输入以下命令检查安装情况：

```bash
jcc --version
```

如果显示版本信息，说明已安装。如果提示"命令未找到"，请联系管理员安装。

### 1.2 了解 jcc CLI

jcc 是 JCWeaver 中间件 v2 HTTP API 的命令行工具，支持：
- 超算任务管理（HPC）
- 智算训练/推理任务
- 云资源管理（容器/虚拟机实例）
- 数据集和模型管理
- HPC 文件包管理

---

## 2. 登录认证

### 2.1 首次登录

```bash
jcc login --username your_username --password your_password
```

**示例**：
```bash
jcc login --username admin --password mypass123
```

### 2.2 登录成功输出

登录成功后会返回 JSON 格式的 token 和 userID：

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "userID": 123
}
```

### 2.3 配置环境变量（推荐）

jcc 支持通过环境变量设置常用全局参数，优先级高于配置文件但低于命令行参数。推荐在登录后一次性设置 `JCC_TOKEN` 和 `JCC_USER_ID`。

| 环境变量 | 对应参数 | 说明 |
|----------|----------|------|
| `JCC_BASE_URL` | `--base-url` | 中间件根 URL，默认 `https://jcc.jointcloud.net` |
| `JCC_JCC_URL` | `--jcc-url` | JCC 根 URL，用于 `/pcm/*` 和 `/jsm/*` 端点 |
| `JCC_TOKEN` | `--token` | 登录后返回的 Authorization token |
| `JCC_USER_ID` | `--user-id` | 登录后返回的用户 ID |

**Windows PowerShell 自动设置示例**：

```powershell
$login = jcc login --username admin --password mypass123 | ConvertFrom-Json
$env:JCC_TOKEN = $login.token
$env:JCC_USER_ID = $login.userID
```

设置完成后，所有子命令可直接执行，无需再传 `--token` 和 `--user-id`：

```bash
jcc aitrain query
jcc hpc status --task-name my-task
```

---

## 3. 快速上手示例

### 示例1：提交超算任务

**步骤1**：创建 HPC 文件包
```bash
jcc commonfile create --name my-vasp-data --path ./vasp-files/ --cluster-name HPCSlurmArm
```

**步骤2**：提交超算任务
```bash
jcc hpc create --name vasp-task-001 --cluster-name HPCSlurmArm --package-name my-vasp-data --apptype vasp
```

**步骤3**：查看任务状态
```bash
jcc hpc status --task-name vasp-task-001 --output text
```

**步骤4**：查看任务日志
```bash
jcc hpc log --task-name vasp-task-001
```

**步骤5**：查看任务结果
```bash
jcc hpc result --task-name vasp-task-001
```

---

### 示例2：提交智算训练任务

**步骤1**：创建数据集
```bash
jcc dataset create --name training-data --path ./dataset/ --clusters ai-cluster-01
```

**步骤2**：创建模型（可选）
```bash
jcc model create --name base-model --path ./model/ --clusters ai-cluster-01
```

**步骤3**：提交训练任务
```bash
jcc aitrain submit \
  --name llama2-training \
  --cluster-name ai-cluster-01 \
  --code-name <父算法/子算法> \
  --dataset-name training-data \
  --model-name base-model \
  --image-name <name> \
  --resource GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1
```

**步骤4**：查询训练任务列表
```bash
jcc aitrain query
```

**步骤5**：查看训练详情
```bash
jcc aitrain detail --task-id <task-id>
```

---

### 示例3：管理云容器

**步骤1**：查询容器列表
```bash
jcc cloudcontainer query --page-num 1 --page-size 10
```

**步骤2**：查看指定容器详情
```bash
jcc cloudcontainer query --id container-xxx
```

**步骤3**：查看容器日志
```bash
jcc cloudcontainer log --name container-xxx
```

**步骤4**：删除容器
```bash
jcc cloudcontainer delete --name container-xxx
```

---

## 4. 常用命令速查

### 登录管理
| 命令 | 说明 |
|------|------|
| `jcc login --username <user> --password <pwd>` | 用户登录 |

### 超算任务
| 命令 | 说明 |
|------|------|
| `jcc hpc create --name <name> --cluster-name <cluster> --package-name <pkg> --apptype <app>` | 创建超算任务 |
| `jcc hpc status --task-name <name>` | 查询任务状态 |
| `jcc hpc log --task-name <name>` | 查询任务日志 |
| `jcc hpc result --task-name <name>` | 查询任务结果 |

### 智算训练
| 命令 | 说明 |
|------|------|
| `jcc aitrain submit --name <name> --cluster-name <name> --code-name <父算法/子算法> --dataset-name <name> --model-name <name> --image-name <name> --resource <TYPE:TYPE:NUMBER*NAME(显存:XXGiB),CPU:N,内存:NGiB:1>` | 提交训练任务 |
| `jcc aitrain query` | 查询训练列表 |
| `jcc aitrain detail --task-id <id>` | 查询训练详情 |
| `jcc aitrain log --task-id <id>` | 查询训练日志 |

### 智算推理
| 命令 | 说明 |
|------|------|
| `jcc aiinfer submit --name <name> --cluster-name <name> --code-name <父算法/子算法> --model-name <name> --image-name <name> --binding-id <id> --resource <TYPE:TYPE:NUMBER*NAME(显存:XXGiB),CPU:N,内存:NGiB:1>` | 提交推理任务 |
| `jcc aiinfer query` | 查询推理列表 |
| `jcc aiinfer stop --task-id <id>` | 停止推理任务 |

### 云资源
| 命令 | 说明 |
|------|------|
| `jcc cloudcontainer create --cluster-name <name> --name <name> --image <image> --cpu <cpu> --memory <memory>` | 创建容器 |
| `jcc cloudcontainer query` | 查询容器 |
| `jcc cloudcontainer log --name <name>` | 容器日志 |
| `jcc cloudcontainer delete --name <name>` | 删除容器 |
| `jcc cloudinstance create --cluster-name <name> --host-name <name> --instance-type <type> --password <pwd> --image-id <img> --security-group-id <sg> --system-disk-size <size>` | 创建虚拟机实例 |
| `jcc cloudinstance query` | 查询虚拟机实例 |
| `jcc cloudinstance operate --host-name <name> --operate-type <type>` | 操作虚拟机实例(start/stop/delete) |

### 数据集/模型
| 命令 | 说明 |
|------|------|
| `jcc dataset create --name <name> --path <path>` | 创建数据集 |
| `jcc dataset query` | 查询数据集 |
| `jcc model create --name <name> --path <path>` | 创建模型 |
| `jcc model query` | 查询模型 |

---

## 5. 环境变量配置

### 常用环境变量

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `JCC_BASE_URL` | 中间件根 URL | https://jcc.jointcloud.net |
| `JCC_JCC_URL` | JCC 根 URL | - |
| `JCC_TOKEN` | Authorization token | - |
| `JCC_USER_ID` | 用户 ID | - |

### PowerShell 配置示例

```powershell
# 设置环境变量
$env:JCC_BASE_URL = "https://jcc.jointcloud.net"
$env:JCC_TOKEN = "your-token-here"
$env:JCC_USER_ID = "123"

# 验证配置
jcc aitrain query
```

---

## 6. 常见问题

### Q1：提示"未登录"或"401 Unauthorized"

**解决方案**：先执行登录命令获取 token：
```bash
jcc login --username your_username --password your_password
```

### Q2：提交任务时提示参数错误

**解决方案**：
1. 检查必填参数是否齐全
2. 确认 ID 是否正确
3. 使用 `--help` 查看命令用法：
   ```bash
   jcc aitrain submit --help
   ```

### Q3：任务提交后状态一直是 pending

**解决方案**：这是正常现象，任务需要排队等待资源。可以使用以下命令查看状态：
```bash
jcc aitrain query
```

### Q4：如何查看详细的错误信息

**解决方案**：使用 `--verbose` 参数获取详细信息：
```bash
jcc aitrain submit --name mytask --cluster-name xxx --code-name <父算法/子算法> --dataset-name <name> --model-name <name> --image-name <name> --resource GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1 --verbose
```

### Q5：如何测试命令而不实际执行

**解决方案**：使用 `--dry-run` 参数仅打印请求体：
```bash
jcc hpc create --name test --cluster-name HPCSlurmArm --package-name test-pkg --apptype vasp --dry-run
```

---

## 7. 获取帮助

### 查看命令帮助

```bash
# 查看所有命令
jcc --help

# 查看特定命令帮助
jcc aitrain --help

# 查看子命令帮助
jcc aitrain submit --help
```

### 输出格式切换

默认输出 JSON 格式，如需文本格式：

```bash
jcc aitrain query --output text
```

---

## 下一步

恭喜你完成快速入门！现在你可以：

1. **深入学习**：查看详细的参考文档
   - [jcc-cli-man.md](file:///~/Desktop/jointcloudskill/.trae/skills/云际计算skill/reference/jcc-cli-man.md) - CLI 完整手册
   - [workflow.md](file:///~/Desktop/jointcloudskill/.trae/skills/云际计算skill/reference/workflow.md) - 工作流列表

2. **尝试工作流**：参考 `reference/workflow/` 目录中的工作流文档

3. **查看参数说明**：参考 `reference/para/` 目录中的参数文档

4. **查看步骤文档**：参考 `reference/step/` 目录中的步骤文档

---

**祝你使用愉快！** 🚀
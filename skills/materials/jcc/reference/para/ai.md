# 智算任务参数

## 概述

本文件定义提交智算任务（大模型训练/推理）所需的参数。

## 训练任务参数

### 参数列表

| 参数名 | 类型 | 必填 | 说明 | 默认值 |
|--------|------|------|------|--------|
| name | string | 是 | 任务名称 | - |
| cluster_name | string | 是 | 目标集群名称。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| code_name | string | 是 | 代码/算法名称。格式为 `父算法名称/子算法名称`，例如 `finetuning02/finetuning0201`。需先选择父算法，再查询并选择该父算法下的子算法，组合后作为 `--code-name` 参数值。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| dataset_name | string | 是 | 数据集名称。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| model_name | string | 否 | 模型名称。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| image_name | string | 是 | 镜像名称。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| resource | string[] | 是 | 资源规格，完整格式: TYPE:TYPE:NUMBER*NAME(显存:XXGiB),CPU:N,内存:NGiB:1，如 GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1 | - |
| command | string | 否 | 运行命令 | - |
| description | string | 否 | 任务描述 | - |
| env | string[] | 否 | 环境变量 KEY=VALUE | - |
| param | string[] | 否 | 运行参数 KEY=VALUE | - |
| schedule_strategy | string | 否 | 调度策略 | dataLocality |
| data | string | 否 | 完整jobSet JSON | - |
| data_file | string | 否 | 完整jobSet JSON文件 | - |
| local_job_id | string | 否 | 本地任务ID | "1" |

### 输入示例

```json
{
  "name": "train-llama2",
  "cluster_name": "AI-GPU-Cluster",
  "code_name": "finetuning02/finetuning0201",
  "dataset_name": "my-dataset",
  "model_name": "llama2-7b",
  "image_name": "pytorch:2.0",
  "resource": ["GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1"],
  "command": "python train.py",
  "description": "Llama2 fine-tuning",
  "env": ["CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7"],
  "param": ["epochs=3", "batch_size=32"],
  "schedule_strategy": "dataLocality"
}
```

### CLI命令

```bash
jcc aitrain submit \
  --name train-llama2 \
  --cluster-name AI-GPU-Cluster \
  --code-name finetuning02/finetuning0201 \
  --dataset-name my-dataset \
  --model-name llama2-7b \
  --image-name pytorch:2.0 \
  --resource GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1 \
  --command "python train.py" \
  --description "Llama2 fine-tuning" \
  --env CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  --param epochs=3 \
  --param batch_size=32
```

---

### 查询训练集群列表

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| type | string | 是 | 资源类型（METAX-GPGPU/MLU/NPU/ILUVATAR-GPGPU/GCU/GPU/DCU），无默认值，需要用户确定 |

#### CLI命令

```bash
jcc aitrain cluster-list --type GPU
```

---

### 查询推理集群列表

#### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| type | string | 是 | 资源类型（NPU/GPU），无默认值，需要用户确定 |

#### CLI命令

```bash
jcc aiinfer cluster-list --type GPU
```

---

## 推理任务参数

### 参数列表

| 参数名 | 类型 | 必填 | 说明 | 默认值 |
|--------|------|------|------|--------|
| name | string | 是 | 任务名称 | - |
| cluster_name | string | 是 | 目标集群名称。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| code_name | string | 是 | 代码/算法名称。格式为 `父算法名称/子算法名称`，例如 `inference/llama2-7b-chat`。需先选择父算法，再查询并选择该父算法下的子算法，组合后作为 `--code-name` 参数值。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| model_name | string | 是 | 模型名称。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| image_name | string | 否 | 镜像名称。提交任务时必须使用 name 类参数（--cluster-name、--code-name、--dataset-name/--model-name、--image-name），ID 类参数虽返回成功但任务不会入表 | - |
| binding_id | int | 否 | 推理服务绑定ID | - |
| resource | string[] | 是 | 资源规格，完整格式: TYPE:TYPE:NUMBER*NAME(显存:XXGiB),CPU:N,内存:NGiB:1，如 GPU:GPU:1*A100(显存:40GiB),CPU:8,内存:50GiB:1 | - |
| command | string | 否 | 运行命令 | - |
| description | string | 否 | 任务描述 | - |
| env | string[] | 否 | 环境变量 KEY=VALUE | - |
| param | string[] | 否 | 运行参数 KEY=VALUE | - |
| schedule_strategy | string | 否 | 调度策略 | networkPriority |
| resource_id | int | 否 | 资源ID（与 --resource-name 二选一） | - |
| resource_name | string | 否 | 资源名称（与 --resource-id 二选一） | - |
| schedule_type | string | 否 | 调度类型如 QoS | - |
| sub_model_name | string | 否 | 子模型名称 | - |
| only_create | bool | 否 | 仅创建不启动 | false |
| data | string | 否 | 完整jobSet JSON | - |
| data_file | string | 否 | 完整jobSet JSON文件 | - |
| local_job_id | string | 否 | 本地任务ID | "1" |

### 输入示例

```json
{
  "name": "infer-llama2",
  "cluster_name": "AI-NPU-Cluster",
  "code_name": "inference/llama2-7b-chat",
  "model_name": "llama2-7b",
  "image_name": "pytorch:2.0",
  "binding_id": 10,
  "resource": ["NPU:NPU:1*Ascend-910(显存:32GiB),CPU:24,内存:256GiB:1"],
  "command": "python infer.py",
  "description": "Llama2 inference"
}
```

### CLI命令

```bash
jcc aiinfer submit \
  --name infer-llama2 \
  --cluster-name AI-NPU-Cluster \
  --code-name inference/llama2-7b-chat \
  --model-name llama2-7b \
  --image-name pytorch:2.0 \
  --binding-id 10 \
  --resource NPU:NPU:1*Ascend-910(显存:32GiB),CPU:24,内存:256GiB:1 \
  --command "python infer.py"
```
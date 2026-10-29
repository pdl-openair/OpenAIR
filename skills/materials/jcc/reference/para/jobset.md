# 工作流管理参数

## 概述

本文件定义工作流（JobSet）提交任务所需的参数。

## 提交工作流

### 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| data | string | 否 | 完整工作流 JSON 内容 |
| data_file | string | 否 | 完整工作流 JSON 文件路径 |

### 说明

- `data` 和 `data_file` 二选一，`data_file` 优先级更高
- 工作流 JSON 格式需符合平台要求

### CLI命令

```bash
# 使用data参数
jcc jobset submit --data '{"key": "value"}'

# 使用data-file参数
jcc jobset submit --data-file ./workflow.json
```
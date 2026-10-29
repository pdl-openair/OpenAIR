# 数据上传任务参数

## 概述

本文件定义上传数据到云际平台所需的参数。

## 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| local_path | string | 是 | 本地文件/目录路径 |
| remote_path | string | 是 | 云端目标路径 |
| overwrite | boolean | 否 | 是否覆盖已存在文件 |
| recursive | boolean | 否 | 是否递归上传目录 |
| bandwidth_limit | string | 否 | 上传带宽限制 |

## 参数说明

### local_path
- 类型：字符串
- 说明：本地要上传的文件或目录的绝对路径
- 示例："/data/dataset.zip" 或 "/data/images/"

### remote_path
- 类型：字符串
- 说明：云端存储的目标路径
- 示例："bucket/dataset.zip"

### overwrite
- 类型：布尔值
- 说明：当目标路径已存在时是否覆盖
- 默认值：false

### recursive
- 类型：布尔值
- 说明：是否递归上传目录下的所有文件
- 默认值：false

### bandwidth_limit
- 类型：字符串
- 说明：上传带宽限制，支持单位：KB/s, MB/s
- 示例："10MB/s"

## 输入示例

```json
{
  "local_path": "/data/training_data/",
  "remote_path": "my-bucket/training-data/",
  "overwrite": true,
  "recursive": true,
  "bandwidth_limit": "50MB/s"
}
```
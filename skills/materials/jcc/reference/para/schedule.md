# 数据调度任务参数

## 概述

本文件定义调度数据到算力中心所需的参数。

## 参数列表

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| data_path | string | 是 | 云端数据路径 |
| target_center | string | 是 | 目标算力中心ID |
| priority | string | 否 | 调度优先级 |
| sync_mode | boolean | 否 | 是否同步模式 |

## 参数说明

### data_path
- 类型：字符串
- 说明：云端存储中的数据路径
- 示例："bucket/dataset.zip"

### target_center
- 类型：字符串
- 说明：目标算力中心的唯一标识
- 可选值：根据平台可用算力中心动态获取

### priority
- 类型：字符串
- 说明：调度任务优先级
- 可选值："low", "medium", "high"
- 默认值："medium"

### sync_mode
- 类型：布尔值
- 说明：是否等待数据同步完成后再返回
- 默认值：false

## 输入示例

```json
{
  "data_path": "my-bucket/training-data/",
  "target_center": "hpc-center-01",
  "priority": "high",
  "sync_mode": true
}
```
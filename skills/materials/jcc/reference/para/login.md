# 登录任务参数

## 概述

本文件定义登录云际平台所需的参数。

## 参数列表

| 参数名 | 类型 | 必填 | 说明 | 默认值 |
|--------|------|------|------|--------|
| username | string | 是 | 用户名 | admin |
| password | string | 是 | 密码 | - |
| base_url | string | 否 | 服务端地址 | https://jcc.jointcloud.net |

## 参数说明

### username
- 类型：字符串
- 说明：用户在云际平台的登录用户名
- 示例："admin"

### password
- 类型：字符串
- 说明：用户登录密码
- 注意：密码为敏感信息，应妥善保管

### base_url
- 类型：字符串
- 说明：中间件根URL
- 默认值："https://jcc.jointcloud.net"

## 输入示例

```json
{
  "username": "admin",
  "password": "your_password",
  "base_url": "https://jcc.jointcloud.net"
}
```

## CLI命令

```bash
jcc login --username admin --password mypass
```

## 输出示例

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "userID": 123
}
```